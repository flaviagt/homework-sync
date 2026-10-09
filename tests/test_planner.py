"""The create/update/skip decisions — the part that must never duplicate rows or clobber Status."""

from datetime import date, datetime

from homework_sync.config import PropertyNames
from homework_sync.dates import LA
from homework_sync.models import Item, ItemType
from homework_sync.notion import Schema
from homework_sync.planner import ExistingPage, fingerprint, plan_sync, source_values
from homework_sync.state import SyncState, opaque
from homework_sync.sync import NotionTarget, apply

from .conftest import TODAY


def make_item(**overrides) -> Item:
    fields = dict(source_id="event-assignment-1", title="Homework #1", course="DSC 190",
                  type=ItemType.HOMEWORK, due=date(2026, 10, 13),
                  link="https://canvas.example.edu/courses/1/assignments/1", source="canvas")
    fields.update(overrides)
    return Item(**fields)


def page_for(item: Item, page_id="page-1", **value_overrides) -> ExistingPage:
    values = source_values(item)
    values.update(value_overrides)
    return ExistingPage(page_id=page_id, source_id=item.source_id, values=values)


def synced_record(item: Item) -> dict:
    return {opaque(item.source_id): fingerprint(source_values(item))}


def plan(items, existing=(), synced=None, backfill_days=7):
    return plan_sync(list(items), list(existing), synced or {}, TODAY, backfill_days)


def test_new_item_is_created():
    item = make_item()
    p = plan([item])
    assert [c.item for c in p.creates] == [item]
    assert not p.updates


def test_rerun_with_unchanged_source_does_nothing():
    item = make_item()
    p = plan([item], [page_for(item)], synced_record(item))
    assert p.is_empty
    assert p.unchanged == [item]


def test_matches_on_source_id_not_title():
    # Two Canvas assignments with the same title and date are different items.
    first = make_item()
    second = make_item(source_id="event-assignment-2")
    p = plan([first, second], [page_for(first)], synced_record(first))
    assert [c.item for c in p.creates] == [second]


def test_edit_made_in_notion_survives_when_source_unchanged():
    item = make_item()
    edited = page_for(item, title="HW1 — start early!")
    p = plan([item], [edited], synced_record(item))
    assert p.is_empty


def test_source_change_updates_only_the_changed_field():
    old = make_item()
    edited_in_notion = page_for(old, title="HW1 — start early!")
    moved = make_item(due=date(2026, 10, 15))
    p = plan([moved], [edited_in_notion], synced_record(old))
    assert len(p.updates) == 1
    assert p.updates[0].changes == {"due": ("2026-10-13", "2026-10-15")}


def test_first_sync_of_existing_row_fixes_differences():
    # No record of a previous sync (e.g. state file lost): Notion is brought in line.
    item = make_item()
    stale = page_for(item, course="DSC190")
    p = plan([item], [stale], synced={})
    assert p.updates[0].changes == {"course": ("DSC190", "DSC 190")}


def test_row_deleted_in_notion_is_not_recreated():
    item = make_item()
    p = plan([item], [], synced_record(item))
    assert not p.creates
    assert p.skipped_deleted == [item]


def test_backfill_window_only_limits_creates():
    old = make_item(source_id="a", due=date(2026, 9, 1))
    recent = make_item(source_id="b", due=date(2026, 9, 22))
    undated = make_item(source_id="c", due=None)
    p = plan([old, recent, undated])
    assert p.skipped_old == [old]
    assert [c.item for c in p.creates] == [recent, undated]

    # An old item that is already in Notion still gets updates.
    renamed = make_item(source_id="a", due=date(2026, 9, 1), title="Renamed")
    p = plan([renamed], [page_for(old)], synced_record(old))
    assert p.updates[0].changes == {"title": ("Homework #1", "Renamed")}


def test_same_instant_in_different_formats_is_not_a_change():
    item = make_item(due=datetime(2026, 11, 16, 12, 30, tzinfo=LA))
    page = page_for(item, due="2026-11-16T20:30:00.000+00:00")  # how Notion may echo it back
    assert plan([item], [page]).is_empty


def test_row_typed_in_by_hand_is_not_duplicated():
    item = make_item(title="Homework #1: Satellite imagery")
    manual = ExistingPage("manual", "", {"title": "HW1", "course": "DSC 190", "type": None,
                                         "due": "2026-10-13", "link": None})
    p = plan([item], [manual])
    assert not p.creates
    assert p.skipped_similar == [(item, manual)]
    # A different due date means a different item.
    assert plan([make_item(due=date(2026, 10, 20))], [manual]).creates


def test_same_item_from_another_source_is_not_duplicated():
    # Canvas was down yesterday, so the course-website copy of HW1 was created.
    from_site = page_for(make_item(source_id="site-abc", title="HW1", source="cse151b"))
    from_canvas = make_item(source_id="event-assignment-7", title="Homework #1")
    p = plan([from_canvas], [from_site])
    assert not p.creates and p.skipped_similar[0][1] is from_site


def test_timed_notion_due_matches_by_local_date():
    item = make_item(title="Prep", due=datetime(2026, 11, 16, 23, 0, tzinfo=LA))
    # 23:00 PST on Nov 16 is Nov 17 in UTC.
    manual = ExistingPage("m", "", {"title": "Prep", "course": "DSC 190", "type": None,
                                    "due": "2026-11-17T07:00:00.000+00:00", "link": None})
    assert plan([item], [manual]).skipped_similar


def test_duplicate_notion_rows_are_reported_not_touched():
    item = make_item()
    p = plan([item], [page_for(item, "p1"), page_for(item, "p2")], synced_record(item))
    assert [d.page_id for d in p.duplicate_pages] == ["p2"]
    assert p.is_empty


# --- apply(): what actually gets sent to Notion ----------------------------------------

SCHEMA = Schema(PropertyNames(), {"title": "title", "course": "select", "type": "select",
                                  "due": "date", "link": "url", "status": "status",
                                  "source_id": "rich_text"})


class FakeClient:
    def __init__(self):
        self.created, self.updated = [], []

    def create_page(self, data_source_id, properties):
        self.created.append(properties)
        return {"id": "new"}

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))
        return {"id": page_id}


def test_apply_sets_status_only_on_create_and_sends_only_changed_fields(config):
    new = make_item(source_id="new")
    old = make_item(source_id="old")
    moved = make_item(source_id="old", due=date(2026, 10, 20))
    p = plan([new, moved], [page_for(old, page_id="page-old")], synced_record(old))

    client, state = FakeClient(), SyncState()
    result = apply(p, NotionTarget(client, "ds", SCHEMA), state, config)

    assert (result.created, result.updated) == (1, 1)
    created = client.created[0]
    assert created["Status"] == {"status": {"name": "Not started"}}
    assert created["Source ID"]["rich_text"][0]["text"]["content"] == "new"

    page_id, update = client.updated[0]
    assert page_id == "page-old"
    assert set(update) == {"Due"}  # never Status, never untouched fields
    assert update["Due"] == {"date": {"start": "2026-10-20"}}

    # Both are now recorded, so a second identical run is a no-op.
    rerun = plan([new, moved], [page_for(new, "p-new"), page_for(moved, "page-old")], state.synced)
    assert rerun.is_empty


def test_failed_write_is_not_recorded_so_it_retries(config):
    from homework_sync.notion import NotionError

    class FailingClient(FakeClient):
        def create_page(self, data_source_id, properties):
            raise NotionError("Notion API 500")

    state = SyncState()
    result = apply(plan([make_item()]), NotionTarget(FailingClient(), "ds", SCHEMA), state, config)
    assert result.created == 0 and len(result.errors) == 1
    assert state.synced == {}

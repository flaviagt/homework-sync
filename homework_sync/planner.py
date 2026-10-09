"""Decide what to create and update in Notion. Pure logic, no I/O.

For each source item, compare three versions: the source now, the source as of
the last sync (a per-field hash in the state file), and the Notion row.

* No row with this Source ID → create it, unless it's old, it was synced
  before and you deleted it, or a row for the same thing already exists (typed
  in by hand, or from another source): same course, due date and a matching title.
* A field whose source value hasn't changed since the last sync is left alone,
  so edits you make in Notion survive.
* A field whose source value changed is updated if Notion differs.
* Status is never part of an update.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .dates import notion_local_date, same_due, to_notion
from .models import Item, titles_match
from .state import opaque

MANAGED_FIELDS = ("title", "course", "type", "due", "link")

Values = dict[str, str | None]


def source_values(item: Item) -> Values:
    return {"title": item.title, "course": item.course, "type": item.type.value,
            "due": to_notion(item.due), "link": item.link}


def fingerprint(values: Values) -> dict[str, str]:
    return {f: opaque(values[f] or "") for f in MANAGED_FIELDS}


def values_equal(name: str, a: str | None, b: str | None) -> bool:
    if name == "due":
        return same_due(a, b)
    return (a or "") == (b or "")


@dataclass(frozen=True)
class ExistingPage:
    page_id: str
    source_id: str  # "" for rows added by hand
    values: Values

    def looks_like(self, item: Item) -> bool:
        return (self.values.get("course") == item.course
                and notion_local_date(self.values.get("due")) == item.due_date
                and titles_match(self.values.get("title") or "", item.title))


@dataclass
class Create:
    item: Item


@dataclass
class Update:
    page_id: str
    item: Item
    changes: dict[str, tuple[str | None, str | None]]  # field -> (Notion now, new value)


@dataclass
class Plan:
    creates: list[Create] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)
    unchanged: list[Item] = field(default_factory=list)
    skipped_old: list[Item] = field(default_factory=list)
    skipped_deleted: list[Item] = field(default_factory=list)
    skipped_similar: list[tuple[Item, ExistingPage]] = field(default_factory=list)
    duplicate_pages: list[ExistingPage] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.creates and not self.updates


def plan_sync(items: list[Item], existing: list[ExistingPage], synced: dict[str, dict[str, str]],
              today: date, backfill_days: int) -> Plan:
    plan = Plan()
    pages: dict[str, ExistingPage] = {}
    for page in existing:
        if not page.source_id:
            continue
        if page.source_id in pages:
            plan.duplicate_pages.append(page)  # reported, never touched
        else:
            pages[page.source_id] = page

    # Rows that belong to an item in this run are never "someone else's copy".
    claimed = {item.source_id for item in items}
    unowned = [p for p in existing if p.source_id not in claimed]

    cutoff = today - timedelta(days=backfill_days)
    for item in items:
        page = pages.get(item.source_id)
        last_synced = synced.get(opaque(item.source_id))

        if page is None:
            if last_synced is not None:
                plan.skipped_deleted.append(item)
            elif item.due_date is not None and item.due_date < cutoff:
                plan.skipped_old.append(item)
            elif similar := next((p for p in unowned if p.looks_like(item)), None):
                plan.skipped_similar.append((item, similar))
            else:
                plan.creates.append(Create(item))
            continue

        new = source_values(item)
        new_fp = fingerprint(new)
        changes = {}
        for name in MANAGED_FIELDS:
            if last_synced is not None and last_synced.get(name) == new_fp[name]:
                continue  # source unchanged since last sync: keep any edit made in Notion
            if not values_equal(name, page.values.get(name), new[name]):
                changes[name] = (page.values.get(name), new[name])
        if changes:
            plan.updates.append(Update(page.page_id, item, changes))
        else:
            plan.unchanged.append(item)
    return plan

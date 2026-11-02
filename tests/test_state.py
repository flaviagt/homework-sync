import json
from datetime import date, datetime

from homework_sync.dates import LA, infer_year
from homework_sync.models import Item, ItemType
from homework_sync.state import SiteSnapshot, SyncState, item_from_dict, item_to_dict, opaque


def test_state_round_trips_and_is_deterministic(tmp_path):
    item = Item("site-abc", "HW1", "DSC Capstone", ItemType.HOMEWORK, date(2026, 10, 5), "https://x", "capstone")
    state = SyncState(
        sites={"capstone": SiteSnapshot("capstone", "hash", "parser", [item_to_dict(item)])},
        synced={opaque("event-assignment-1"): {"title": "t"}},
    )
    path = tmp_path / "state.json"
    state.save(path)
    first = path.read_text()
    SyncState.load(path).save(path)
    assert path.read_text() == first  # no timestamps → no spurious commits

    loaded = SyncState.load(path)
    assert loaded.sites["capstone"].to_items() == [item]
    assert loaded.synced == state.synced


def test_no_canvas_data_in_the_state_file(tmp_path):
    state = SyncState(synced={opaque("event-assignment-9990001"): {"title": opaque("Quiz #1")}})
    path = tmp_path / "state.json"
    state.save(path)
    text = path.read_text()
    assert "9990001" not in text and "Quiz" not in text
    assert json.loads(text)["version"] == 1


def test_timed_items_round_trip():
    item = Item("x", "Prep", "MGT 16", ItemType.HOMEWORK, datetime(2026, 11, 16, 12, 30, tzinfo=LA))
    assert item_from_dict(item_to_dict(item), "canvas").due == item.due


def test_year_inference_picks_the_nearest_year():
    assert infer_year(10, 5, date(2026, 9, 27)) == date(2026, 10, 5)
    assert infer_year(1, 11, date(2026, 9, 27)) == date(2027, 1, 11)   # winter quarter, read in fall
    assert infer_year(12, 1, date(2027, 1, 10)) == date(2026, 12, 1)   # last month, read in January

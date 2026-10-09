"""The state file the GitHub Action commits back after each run.

It holds two things:

* ``sites``: a snapshot of each course website's items (public data), used to
  report what's new since the last run and to reuse a Claude extraction when
  the page hasn't changed.
* ``synced``: for every item ever synced, a hash of each field's value as last
  written to Notion. Keys and values are opaque hashes, so no Canvas data ends
  up in the public repo. This lets the planner tell "the source changed" apart
  from "you edited the row in Notion".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .dates import to_notion
from .models import Item, ItemType

VERSION = 1


def opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def item_to_dict(item: Item) -> dict:
    return {"source_id": item.source_id, "title": item.title, "course": item.course,
            "type": item.type.value, "due": to_notion(item.due), "link": item.link}


def item_from_dict(d: dict, source: str) -> Item:
    due = d.get("due")
    parsed = (datetime.fromisoformat(due) if "T" in due else date.fromisoformat(due)) if due else None
    return Item(source_id=d["source_id"], title=d["title"], course=d["course"],
                type=ItemType(d["type"]), due=parsed, link=d.get("link"), source=source)


@dataclass
class SiteSnapshot:
    key: str
    page_hash: str
    method: str
    items: list[dict] = field(default_factory=list)

    def to_items(self) -> list[Item]:
        return [item_from_dict(d, self.key) for d in self.items]

    def source_ids(self) -> set[str]:
        return {d["source_id"] for d in self.items}


@dataclass
class SyncState:
    sites: dict[str, SiteSnapshot] = field(default_factory=dict)
    # opaque(source_id) -> {field: opaque(value last written to Notion)}
    synced: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        if raw.get("version") != VERSION:
            return cls()
        return cls(
            sites={k: SiteSnapshot(key=k, **v) for k, v in raw.get("sites", {}).items()},
            synced=raw.get("synced", {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": VERSION,
            "sites": {k: {"page_hash": s.page_hash, "method": s.method, "items": s.items}
                      for k, s in sorted(self.sites.items())},
            "synced": dict(sorted(self.synced.items())),
        }
        # Sorted and without timestamps, so the file (and the repo) only changes
        # when something real changes.
        path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

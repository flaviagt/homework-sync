"""Shared CSV format for the assignment exports (Gradescope, Canvas)."""

from __future__ import annotations

import csv
from datetime import date, datetime, time
from pathlib import Path

from .dates import LA

COLUMNS = ["Course", "Assignment", "Due", "Day", "Status", "Late due", "Released", "Link"]


def fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def as_datetime(due: date | datetime | None) -> datetime | None:
    """Canvas exports "due at 11:59 PM" as an all-day date; show it as 23:59."""
    if due is None or isinstance(due, datetime):
        return due
    return datetime.combine(due, time(23, 59), tzinfo=LA)


def row(course: str, title: str, due: datetime | None, link: str, status: str = "",
        late_due: datetime | None = None, released: datetime | None = None) -> dict:
    return {"Course": course, "Assignment": title, "Due": fmt(due),
            "Day": due.strftime("%a") if due else "", "Status": status,
            "Late due": fmt(late_due), "Released": fmt(released), "Link": link}


def sort_rows(rows: list[dict]) -> list[dict]:
    # Undated assignments last, then by due date ("YYYY-MM-DD HH:MM" sorts as text).
    return sorted(rows, key=lambda r: (not r["Due"], r["Due"], r["Course"], r["Assignment"]))


def write_and_print(rows: list[dict], out: Path, source_label: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows accents and symbols correctly.
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} assignments from {source_label}\n")
    width = max((len(r["Assignment"]) for r in rows), default=10)
    for r in rows:
        print(f"  {r['Day']:<4}{r['Due'] or '(no due date)':<17} {r['Course']:<13} "
              f"{r['Assignment']:<{width}}  {r['Status']}".rstrip())
    print(f"\nSaved to {out}")

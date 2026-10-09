"""Dedup across and within sources, before anything reaches Notion."""

from __future__ import annotations

from .models import Item, titles_match


def drop_site_duplicates_of_canvas(canvas: list[Item], site_items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Drop course-website items that Canvas already has.

    A site item is a duplicate when a Canvas item has the same course, the same
    due date, and a matching title ("HW1" vs "Homework #1: ..."). Canvas wins
    because its link goes straight to the submission page.

    Returns (kept, dropped).
    """
    kept, dropped = [], []
    for item in site_items:
        if any(c.course == item.course and c.due_date == item.due_date
               and titles_match(c.title, item.title) for c in canvas):
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped


def unique_by_source_id(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Keep the first item per Source ID. Returns (kept, dropped)."""
    seen: set[str] = set()
    kept, dropped = [], []
    for item in items:
        (dropped if item.source_id in seen else kept).append(item)
        seen.add(item.source_id)
    return kept, dropped

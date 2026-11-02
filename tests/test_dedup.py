from datetime import date

import pytest

from homework_sync.dedup import drop_site_duplicates_of_canvas, unique_by_source_id
from homework_sync.models import Item, ItemType, canonical_code, site_source_id, titles_match


def item(title, course="DSC 190", due=date(2026, 10, 5), source_id=None, source="canvas"):
    return Item(source_id=source_id or title, title=title, course=course,
                type=ItemType.HOMEWORK, due=due, source=source)


@pytest.mark.parametrize("title, code", [
    ("HW1", "hw1"),
    ("Homework #1: Satellite imagery", "hw1"),
    ("PA 2", "pa2"),
    ("Quiz #3 (Ethics Definitions)", "quiz3"),
    ("Final project milestone #4: Final paper", "milestone4"),
    ("Ripped from the Headlines #1", None),
])
def test_canonical_code(title, code):
    assert canonical_code(title) == code


@pytest.mark.parametrize("a, b, same", [
    ("HW1", "Homework #1: Backprop", True),
    ("HW1", "PA1", False),
    ("Midterm", "Midterm Exam", True),
    ("Read the Syllabus", "Syllabus", True),
    ("Corporate Ethics Project", "Behavioral Labs", False),
])
def test_titles_match(a, b, same):
    assert titles_match(a, b) is same


def test_site_item_already_in_canvas_is_dropped():
    canvas = [item("Homework #1: Backprop")]
    site = [item("HW1", source="capstone"), item("PA1", source="capstone")]
    kept, dropped = drop_site_duplicates_of_canvas(canvas, site)
    assert [i.title for i in kept] == ["PA1"]
    assert [i.title for i in dropped] == ["HW1"]


@pytest.mark.parametrize("site_item", [
    item("HW1", due=date(2026, 10, 6), source="capstone"),     # different due date
    item("HW1", course="MGT 16", source="capstone"),           # different course
])
def test_similar_items_that_differ_are_kept(site_item):
    kept, _ = drop_site_duplicates_of_canvas([item("Homework #1")], [site_item])
    assert kept == [site_item]


def test_unique_by_source_id_keeps_first():
    a, b = item("A", source_id="x"), item("B", source_id="x")
    assert unique_by_source_id([a, b]) == ([a], [b])


def test_site_source_id_ignores_case_and_punctuation_but_not_page():
    url = "https://example.edu/course"
    assert site_source_id(url, "HW1") == site_source_id(url, "hw1 ")
    assert site_source_id(url, "HW1") != site_source_id("https://example.edu/other", "HW1")

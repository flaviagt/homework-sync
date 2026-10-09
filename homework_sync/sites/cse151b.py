"""Parser for the CSE 151B course site's weekly schedule table.

Columns (by header text): Week / dates | Monday quiz | Tuesday / Thursday
lectures | Homework & Programming Assignments | Weekly learning goal | Project
milestones. Date labels look like "Tue · Sep 29", "Released · Sep 28", "Due · Oct 5".
"""

from __future__ import annotations

import re
from datetime import date

from bs4 import Tag

from ..config import SiteConfig
from ..dates import first_date
from ..models import Item, ItemType, classify_title
from .base import LayoutChanged, RawItem, header_labels, make_items, soup, split_lines

_ASSIGNMENT_CODE = re.compile(r"\b(HW|PA)\s*(\d+)\b", re.I)
_QUIZ = re.compile(r"\bquiz", re.I)
_READING_LINE = re.compile(r"^\s*(pre-?)?read(ing)?s?\b", re.I)
_EMPTY = {"", "—", "-", "–", "tbd", "none"}


def parse(html: str, site: SiteConfig, today: date) -> list[Item]:
    doc = soup(html)
    table = next((t for t in doc.find_all("table")
                  if any("homework" in label.lower() for label in header_labels(t))), None)
    if table is None:
        raise LayoutChanged("no schedule table with a 'Homework' column")
    labels = [label.lower() for label in header_labels(table)]

    raws: list[RawItem] = []
    body = table.find("tbody") or table
    for row in body.find_all("tr", recursive=False):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or all(c.name == "th" for c in cells):
            continue
        for label, cell in zip(labels, cells):
            if "homework" in label or "assignment" in label:
                raws += _assignments(cell, site, today)
            elif "quiz" in label:
                raws += _quizzes(cell, today)
            elif "lecture" in label:
                raws += _lectures(cell, site, today)
            elif "milestone" in label:
                raws += _milestones(cell, site, today)

    raws += _exams_outside(doc, table, today)
    return make_items(raws, site)


def _paragraphs(cell: Tag) -> list[Tag]:
    return cell.find_all("p") or [cell]


def _date_labels(p: Tag) -> dict[str, str]:
    """{"released": "Released · Sep 28", "due": "Due · Oct 5", ...} for a paragraph."""
    labels = {}
    for span in p.find_all(class_="date"):
        text = span.get_text(" ", strip=True)
        labels[text.split("·")[0].strip().lower()] = text
    return labels


def _assignments(cell: Tag, site: SiteConfig, today: date) -> list[RawItem]:
    raws = []
    for p in _paragraphs(cell):
        due = first_date(_date_labels(p).get("due", ""), today)
        event = p.find(class_="event")
        if event is None:
            continue
        for line in split_lines(event, site.url):
            if line.text.lower() in _EMPTY:
                continue
            ungraded = " (ungraded)" if "ungraded" in line.text.lower() else ""
            link = line.links[0] if line.links else None
            codes = _ASSIGNMENT_CODE.findall(line.text)
            # "HW1 & PA1" is two separate submissions.
            titles = ([f"{kind.upper()}{num}{ungraded}" for kind, num in codes]
                      or [line.text])
            raws += [RawItem(t, classify_title(t), due, link) for t in titles]
    return raws


def _quizzes(cell: Tag, today: date) -> list[RawItem]:
    raws = []
    for p in _paragraphs(cell):
        text = p.get_text(" ", strip=True)
        if not _QUIZ.search(text):  # e.g. "Foundations clinic" is a discussion, not a quiz
            continue
        date_label = next(iter(_date_labels(p).values()), text)
        due = first_date(date_label, today)
        # "Oct 5 · Quiz 1" → "Quiz 1"; fall back to the whole paragraph.
        title = date_label.split("·", 1)[1].strip() if "·" in date_label else text
        if not _QUIZ.search(title):
            title = f"Quiz: {title}"
        raws.append(RawItem(title, ItemType.QUIZ, due))
    return raws


def _lectures(cell: Tag, site: SiteConfig, today: date) -> list[RawItem]:
    raws = []
    for p in _paragraphs(cell):
        lecture_date = first_date(next(iter(_date_labels(p).values()), ""), today)
        heading = p.find("b")
        topic = heading.get_text(" ", strip=True) if heading else ""
        if topic and classify_title(topic) == ItemType.TEST:
            raws.append(RawItem(topic, ItemType.TEST, lecture_date))
        # Readings listed under a lecture are due before that lecture.
        for line in split_lines(p, site.url):
            if _READING_LINE.match(line.text):
                link = line.links[0] if line.links else None
                raws.append(RawItem(line.text, ItemType.READING, lecture_date, link))
    return raws


def _milestones(cell: Tag, site: SiteConfig, today: date) -> list[RawItem]:
    raws = []
    for p in _paragraphs(cell):
        labels = _date_labels(p)
        for span in p.find_all(class_="date"):
            span.extract()
        for line in split_lines(p, site.url):
            if line.text.lower() in _EMPTY:
                continue
            due = first_date(labels.get("due", ""), today) or first_date(line.text, today)
            link = line.links[0] if line.links else None
            raws.append(RawItem(line.text, classify_title(line.text), due, link))
    return raws


def _exams_outside(doc: Tag, table: Tag, today: date) -> list[RawItem]:
    """Exams announced in the page header, e.g. "<b>Midterm</b> In class on Oct 20"."""
    raws = []
    for b in doc.find_all(["b", "strong"]):
        if table in b.parents:
            continue
        title = b.get_text(" ", strip=True)
        if classify_title(title) != ItemType.TEST:
            continue
        when = first_date(b.parent.get_text(" ", strip=True), today)
        if when:
            raws.append(RawItem(title, ItemType.TEST, when))
    return raws

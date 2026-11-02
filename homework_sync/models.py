"""Core data model shared by every source and the Notion sync."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from enum import Enum


class ItemType(str, Enum):
    """Values of the Notion "Type" select."""

    HOMEWORK = "Homework"
    READING = "Reading"
    QUIZ = "Quiz"
    TEST = "Test"


Due = date | datetime | None


@dataclass(frozen=True)
class Item:
    """One thing to do, normalized from any source.

    ``source_id`` is the stable identity used for Notion dedup: the ICS UID for
    Canvas items, or a hash of (page URL, item key) for course-website items.
    """

    source_id: str
    title: str
    course: str
    type: ItemType
    due: Due = None
    link: str | None = None
    source: str = ""
    # Canvas data stays out of public CI logs; see cli.describe().
    private: bool = False

    @property
    def due_date(self) -> date | None:
        if isinstance(self.due, datetime):
            return self.due.date()
        return self.due


_TEST = re.compile(r"\b(midterm|final exam|exam)\b", re.I)
# "Midterm report" and "Midterm review" mention an exam but aren't one.
_NOT_AN_EXAM = re.compile(
    r"\b(report|paper|project|proposal|presentation|essay|milestone|homework|assignment|"
    r"write-?up|poster|review|prep|preparation|solutions?|recap|feedback|form|survey)\b", re.I)
_QUIZ = re.compile(r"\bquiz(zes)?\b", re.I)
_READING = re.compile(r"^\s*(pre-?)?(read|reading|readings)\b", re.I)


def classify_title(title: str, default: ItemType | None = ItemType.HOMEWORK) -> ItemType | None:
    """Guess the item type from its title; falls back to ``default``."""
    if _TEST.search(title) and not _NOT_AN_EXAM.search(title):
        return ItemType.TEST
    if _QUIZ.search(title):
        return ItemType.QUIZ
    if _READING.search(title):
        return ItemType.READING
    return default


def normalize_title(title: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — used for matching."""
    title = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


_CODE = re.compile(
    r"\b(hw|homework|pa|quiz|project milestone|milestone|lab|problem set|ps|assignment)"
    r"\s*#?\s*(\d+)\b", re.I)
_CODE_ALIASES = {"homework": "hw", "project milestone": "milestone", "problem set": "ps"}


def canonical_code(title: str) -> str | None:
    """Short code like "hw1", "pa2", "quiz3" or "milestone4", if the title has one."""
    m = _CODE.search(title)
    if not m:
        return None
    kind = m.group(1).lower()
    return f"{_CODE_ALIASES.get(kind, kind)}{int(m.group(2))}"


def titles_match(a: str, b: str) -> bool:
    """Whether two titles plausibly name the same item ("HW1" vs "Homework #1: ...")."""
    code_a, code_b = canonical_code(a), canonical_code(b)
    if code_a and code_b:
        return code_a == code_b
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) >= 6 and shorter in longer:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.85


def item_key(title: str) -> str:
    """Stable per-page identifier for a site item: its code if it has one, else its title."""
    return canonical_code(title) or normalize_title(title)


def site_source_id(page_url: str, key: str) -> str:
    """Stable Source ID for a course-website item.

    ``key`` is the item's stable identifier on the page (e.g. "hw1" or its
    normalized title). The page URL is used rather than the item's own link,
    because links often get added or change after an item is first posted.
    """
    digest = hashlib.sha256(f"{page_url}|{normalize_title(key)}".encode()).hexdigest()
    return f"site-{digest[:16]}"

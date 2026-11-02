"""Date helpers. Everything is interpreted in the course's time zone."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .models import Due

LA = ZoneInfo("America/Los_Angeles")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_DAY = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)


def today_local() -> date:
    return datetime.now(LA).date()


def infer_year(month: int, day: int, today: date) -> date:
    """Course sites write "Oct 5" with no year: pick the year closest to today.

    A fall-quarter page read in September resolves "Jan 11" to next January,
    and a winter page read in January resolves "Dec 1" to last December.
    """
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # Feb 29 in a non-leap year
            continue
    return min(candidates, key=lambda d: abs((d - today).days))


def find_dates(text: str, today: date) -> list[date]:
    """All "Mon DD" style dates in ``text``, in order of appearance."""
    found = []
    for month, day in _MONTH_DAY.findall(text):
        try:
            found.append(infer_year(_MONTHS[month[:3].lower()], int(day), today))
        except ValueError:
            continue
    return found


def first_date(text: str, today: date) -> date | None:
    dates = find_dates(text, today)
    return dates[0] if dates else None


def to_notion(due: Due) -> str | None:
    """ISO string for the Notion date property (date-only or with an offset)."""
    if due is None:
        return None
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=LA)
        return due.astimezone(LA).isoformat()
    return due.isoformat()


def same_due(a: str | None, b: str | None) -> bool:
    """Compare two Notion date strings by meaning rather than by text.

    Notion may echo ``2026-10-16T13:30:00-07:00`` back as
    ``2026-10-16T20:30:00.000+00:00``; both are the same instant.
    """
    if not a or not b:
        return not a and not b
    a_is_dt, b_is_dt = "T" in a, "T" in b
    if a_is_dt != b_is_dt:
        return False
    if not a_is_dt:
        return date.fromisoformat(a) == date.fromisoformat(b)
    return _parse_dt(a) == _parse_dt(b)


def notion_local_date(s: str | None) -> date | None:
    """The calendar date (in LA) of a Notion date string."""
    if not s:
        return None
    return _parse_dt(s).astimezone(LA).date() if "T" in s else date.fromisoformat(s)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=LA)


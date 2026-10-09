"""Canvas calendar feed (.ics) → Items.

The feed URL embeds a private token, so it is never logged; fetch errors are
re-raised without it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

import requests
from icalendar import Calendar

from .config import Config
from .dates import LA
from .models import Item, ItemType, classify_title

_COURSE_SUFFIX = re.compile(r"^(.*?)\s*\[([^\[\]]+)\]\s*$")
# Canvas prefixes titles with the item kind, which the Type column already shows.
_KIND_PREFIX = re.compile(r"^(Assignment|Assessment):\s*", re.I)


class CanvasFetchError(RuntimeError):
    pass


def fetch_ics(url: str, session: requests.Session | None = None) -> bytes:
    session = session or requests.Session()
    try:
        resp = session.get(url, timeout=30, headers={"User-Agent": "homework-sync"})
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise CanvasFetchError(
            f"Canvas feed returned HTTP {e.response.status_code}. If this is 401/404, "
            "the feed URL was probably reset — copy a new one from Canvas → Calendar → "
            "Calendar Feed and update the CANVAS_ICS_URL secret.") from None
    except requests.RequestException as e:
        # str(e) includes the URL, so only the exception type is reported.
        raise CanvasFetchError(f"Could not reach the Canvas feed ({type(e).__name__}).") from None
    return resp.content


def parse_ics(data: bytes, config: Config) -> list[Item]:
    items = []
    for event in Calendar.from_ical(data).walk("VEVENT"):
        item = _event_to_item(event, config)
        if item is not None:
            items.append(item)
    return items


def _event_to_item(event, config: Config) -> Item | None:
    uid = str(event.get("UID", "")).strip()
    summary = str(event.get("SUMMARY", "")).strip()
    if not uid or not summary:
        return None

    m = _COURSE_SUFFIX.match(summary)
    title, code = (m.group(1), m.group(2)) if m else (summary, "")
    title = _KIND_PREFIX.sub("", title).strip()

    is_assignment = uid.startswith("event-assignment-")
    item_type = classify_title(title)
    # Plain calendar events are mostly lectures and office hours; keep only
    # the ones that look like exams or quizzes.
    if not is_assignment and item_type not in (ItemType.QUIZ, ItemType.TEST):
        return None

    return Item(
        source_id=uid,
        title=title,
        course=config.course_name(code) if code else "Canvas",
        type=item_type,
        due=_due(event),
        link=_link(str(event.get("URL", "")) or None),
        source="canvas",
        private=True,
    )


def _due(event) -> date | datetime | None:
    prop = event.get("DTSTART")
    if prop is None:
        return None
    value = prop.dt
    if isinstance(value, datetime):
        # Canvas sends UTC; "floating" times are treated as local.
        return (value if value.tzinfo else value.replace(tzinfo=LA)).astimezone(LA)
    # All-day events are how Canvas exports "due at 11:59 PM".
    return value


def _link(url: str | None) -> str | None:
    """Turn the feed's calendar URL into a direct assignment link.

    Feed URLs look like
    ``https://canvas.ucsd.edu/calendar?include_contexts=course_123&month=10&year=2026#assignment_456``.
    """
    if not url:
        return None
    parts = urlsplit(url)
    course = re.fullmatch(r"course_(\d+)", parse_qs(parts.query).get("include_contexts", [""])[0])
    assignment = re.fullmatch(r"assignment_(\d+)", parts.fragment)
    if course and assignment:
        return f"{parts.scheme}://{parts.netloc}/courses/{course.group(1)}/assignments/{assignment.group(1)}"
    return url

"""Parser for the DSC Capstone (DSC 180AB) site's schedule tables.

Columns: Week | Methodology (spans two columns) | Domain | Project. Each cell
holds short lines separated by <br>. The page's stated policy is that lessons
and assignments are released on Mondays and assignments are due a week later,
so undated assignments get "row date + 7 days".
"""

from __future__ import annotations

import re
from datetime import date

from ..config import SiteConfig
from ..dates import find_dates, first_date
from ..models import Item, ItemType, classify_title
from .base import LayoutChanged, Line, RawItem, header_labels, make_items, plus_days, soup, split_lines

_LIVE_SESSION = re.compile(r"^\s*live\b|\blive on\b", re.I)
_HOMEWORK_WORDS = re.compile(
    r"\b(assignment|due|submit|checkpoint|milestone|participation|report|proposal|"
    r"presentation|deliverable|poster|write-?up)\b", re.I)
_DUE_PHRASE = re.compile(r"\bdue\b(.*)", re.I)
_EMPTY = {"", "—", "-", "–", "tbd", "none"}


def parse(html: str, site: SiteConfig, today: date) -> list[Item]:
    doc = soup(html)
    tables = [t for t in doc.find_all("table")
              if (labels := header_labels(t)) and labels[0].lower().startswith("week")]
    if not tables:
        raise LayoutChanged("no schedule table whose first column is 'Week'")

    raws: list[RawItem] = []
    for table in tables:
        labels = header_labels(table)
        body = table.find("tbody") or table
        for row in body.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            # The row's anchor date, e.g. "Live on Monday, Oct 5".
            anchor = first_date(row.get_text(" ", strip=True), today)
            for label, cell in zip(labels[1:], cells[1:]):
                for line in split_lines(cell, site.url):
                    raw = _line_to_item(line, label, anchor, today)
                    if raw:
                        raws.append(raw)
    return make_items(raws, site)


def _line_to_item(line: Line, column: str, anchor: date | None, today: date) -> RawItem | None:
    text = line.text
    if text.lower() in _EMPTY or _LIVE_SESSION.search(text):
        return None
    link = line.links[0] if line.links else None

    guess = classify_title(text, default=None)
    if guess == ItemType.READING or (link and "/lessons/" in link):
        item_type = ItemType.READING
    elif guess in (ItemType.TEST, ItemType.QUIZ):
        item_type = guess
    elif column.lower().startswith("project") or _HOMEWORK_WORDS.search(text):
        item_type = ItemType.HOMEWORK
    elif link:
        item_type = ItemType.READING  # linked course material to work through
    else:
        return None  # a note, not an item

    due_phrase = _DUE_PHRASE.search(text)
    explicit = find_dates(due_phrase.group(1), today) if due_phrase else []
    if explicit:
        due = explicit[0]
    elif item_type == ItemType.HOMEWORK:
        due = plus_days(anchor, 7)
    else:
        due = anchor  # readings: finish before that week's session
    return RawItem(text, item_type, due, link)

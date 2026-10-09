"""Shared helpers for course-website parsers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from ..config import SiteConfig
from ..models import Due, Item, ItemType, item_key, site_source_id, titles_match


class LayoutChanged(Exception):
    """The page no longer has the structure its parser expects."""


@dataclass
class RawItem:
    """What a parser finds, before it gets a Source ID."""

    title: str
    type: ItemType
    due: Due = None
    link: str | None = None


@dataclass
class Line:
    text: str
    links: list[str] = field(default_factory=list)


_BLOCK_TAGS = {"p", "li", "div", "tr", "br"}


def split_lines(tag: Tag, base_url: str) -> list[Line]:
    """Split a table cell into visual lines (at <br> and block tags), keeping each line's links."""
    lines: list[Line] = []
    text: list[str] = []
    links: list[str] = []

    def flush() -> None:
        joined = re.sub(r"\s+", " ", "".join(text)).strip(" ,;")
        if joined:
            lines.append(Line(joined, list(links)))
        text.clear()
        links.clear()

    for node in tag.descendants:
        if isinstance(node, Tag):
            if node.name in _BLOCK_TAGS:
                flush()
            if node.name == "a" and node.get("href"):
                links.append(urljoin(base_url, node["href"]))
        elif isinstance(node, NavigableString) and not isinstance(node, Comment):
            text.append(str(node))
    flush()
    return lines


def header_labels(table: Tag) -> list[str]:
    """Column labels of a table, with colspans expanded so they line up with body cells."""
    row = table.find("thead") or table
    first = row.find("tr")
    if first is None:
        return []
    labels = []
    for th in first.find_all(["th", "td"], recursive=False):
        # Only the first line of a header, e.g. "Methodology" not its footnote.
        label = th.get_text("\n", strip=True).split("\n")[0]
        labels.extend([label] * int(th.get("colspan", 1) or 1))
    return labels


def make_items(raws: list[RawItem], site: SiteConfig) -> list[Item]:
    """Assign Source IDs, merging duplicates and disambiguating key collisions."""
    merged: list[RawItem] = []
    for raw in raws:
        # The same item can appear twice (e.g. the midterm in the header and the
        # schedule): same type, same date, matching title → keep the first.
        if any(r.type == raw.type and r.due == raw.due and titles_match(r.title, raw.title)
               for r in merged):
            continue
        merged.append(raw)

    keys = [item_key(r.title) for r in merged]
    items = []
    for raw, key in zip(merged, keys):
        if keys.count(key) > 1:  # e.g. "Weekly reflection" every week
            key = f"{key} {raw.due.isoformat() if raw.due else ''}".strip()
        items.append(Item(
            source_id=site_source_id(site.url, key),
            title=raw.title,
            course=site.course,
            type=raw.type,
            due=raw.due,
            link=raw.link or site.url,
            source=site.key,
        ))
    return items


def soup(html: str) -> BeautifulSoup:
    # lxml copes with the implicit end tags (<td>a<td>b) some sites use.
    return BeautifulSoup(html, "lxml")


def page_text(html: str, base_url: str) -> str:
    """Readable text of a page, with links inlined — used for hashing and for Claude."""
    doc = soup(html)
    for tag in doc(["script", "style", "noscript", "svg"]):
        tag.decompose()
    for a in doc.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        a.replace_with(f"{label} <{urljoin(base_url, a['href'])}>" if label else "")
    for cell in doc.find_all(["td", "th"]):
        cell.append(" | ")
    for block in doc.find_all(["tr", "p", "li", "br", "h1", "h2", "h3", "h4", "div"]):
        block.insert_before("\n")
    text = doc.get_text()
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln)


def page_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def plus_days(d: date | None, days: int) -> date | None:
    return d + timedelta(days=days) if d else None

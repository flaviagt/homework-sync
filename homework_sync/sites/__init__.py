"""Course websites without an API: fetch, parse, and fall back to Claude if parsing breaks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

from ..config import SiteConfig
from ..models import Item
from ..state import SiteSnapshot
from . import capstone, cse151b
from .base import LayoutChanged, page_hash, page_text
from .llm import ClaudeExtractor, LLMExtractionError

log = logging.getLogger(__name__)

PARSERS = {
    "cse151b": cse151b.parse,
    "capstone": capstone.parse,
}


class SiteError(RuntimeError):
    pass


@dataclass
class SiteResult:
    items: list[Item]
    method: str  # "parser", "claude", or "claude-cached"
    page_hash: str


def collect_site(site: SiteConfig, *, today: date, session: requests.Session,
                 previous: SiteSnapshot | None, llm: ClaudeExtractor | None) -> SiteResult:
    try:
        resp = session.get(site.url, timeout=30, headers={"User-Agent": "homework-sync"})
        resp.raise_for_status()
    except requests.RequestException as e:
        raise SiteError(f"could not fetch {site.url}: {e}") from None
    html = resp.text
    text = page_text(html, site.url)
    digest = page_hash(text)

    try:
        return SiteResult(PARSERS[site.parser](html, site, today), "parser", digest)
    except LayoutChanged as e:
        reason = str(e)
    log.warning("%s: parser failed (%s); trying the Claude fallback", site.key, reason)

    # Don't pay for the same extraction twice if the page hasn't changed since.
    if previous and previous.page_hash == digest and previous.method.startswith("claude"):
        return SiteResult(previous.to_items(), "claude-cached", digest)
    if llm is None:
        raise SiteError(f"{site.key}: the page layout changed ({reason}) and ANTHROPIC_API_KEY "
                        "isn't set, so the Claude fallback can't run. Update the parser in "
                        f"homework_sync/sites/{site.parser}.py or add the key.")
    try:
        return SiteResult(llm.extract(text, site, today), "claude", digest)
    except LLMExtractionError as e:
        raise SiteError(f"{site.key}: Claude fallback failed: {e}") from None

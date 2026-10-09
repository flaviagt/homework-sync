"""Minimal Notion REST client plus property encoding for the homework database.

Targets Notion API version 2025-09-03, where rows live in a database's *data
source*. The configured database ID is resolved to its data source on startup.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from .config import PropertyNames
from .planner import ExistingPage, Values

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
_TEXT_LIMIT = 2000  # Notion's per-text-object character limit


class NotionError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotionClient:
    def __init__(self, token: str, session: requests.Session | None = None,
                 max_retries: int = 5, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })
        self.max_retries = max_retries
        self.sleep = sleep

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(self.max_retries + 1):
            resp = self.session.request(method, API + path, json=body, timeout=30)
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < self.max_retries:
                delay = float(resp.headers.get("Retry-After") or 2 ** attempt)
                log.debug("Notion %s %s → %s; retrying in %.0fs", method, path, resp.status_code, delay)
                self.sleep(min(delay, 60))
                continue
            if resp.ok:
                return resp.json()
            raise NotionError(_error_message(resp), resp.status_code)
        raise AssertionError("unreachable")

    def resolve_data_source(self, database_id: str) -> str:
        """Database ID (from the Notion URL) → the data source that holds its rows."""
        try:
            sources = self.request("GET", f"/databases/{database_id}").get("data_sources", [])
        except NotionError as e:
            if e.status != 404:
                raise
            # Maybe a data source ID was given directly.
            try:
                return self.request("GET", f"/data_sources/{database_id}")["id"]
            except NotionError:
                raise e from None
        if not sources:
            raise NotionError("The database has no data sources.")
        if len(sources) > 1:
            log.warning("Database has %d data sources; using the first (%s).",
                        len(sources), sources[0].get("name"))
        return sources[0]["id"]

    def properties(self, data_source_id: str) -> dict:
        return self.request("GET", f"/data_sources/{data_source_id}")["properties"]

    def query_all(self, data_source_id: str, filter: dict | None = None) -> list[dict]:
        pages, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if filter:
                body["filter"] = filter
            if cursor:
                body["start_cursor"] = cursor
            resp = self.request("POST", f"/data_sources/{data_source_id}/query", body)
            pages += resp["results"]
            if not resp.get("has_more"):
                return pages
            cursor = resp["next_cursor"]

    def create_page(self, data_source_id: str, properties: dict) -> dict:
        return self.request("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        })

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})


def _error_message(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    message = f"Notion API {resp.status_code} {body.get('code', '')}: {body.get('message', resp.text[:200])}"
    if resp.status_code == 404:
        message += (" — open the database in Notion, click ••• → Connections, and add your "
                    "integration; also check NOTION_DATABASE_ID.")
    elif resp.status_code == 401:
        message += " — check the NOTION_TOKEN secret."
    return message


# --- Schema -------------------------------------------------------------------

_EXPECTED = {
    "title": {"title"},
    "course": {"select", "rich_text"},
    "type": {"select"},
    "due": {"date"},
    "link": {"url"},
    "status": {"status", "select"},
    "source_id": {"rich_text"},
}


class SchemaError(NotionError):
    pass


@dataclass(frozen=True)
class Schema:
    names: PropertyNames
    types: dict[str, str]  # logical field -> Notion property type

    @classmethod
    def validate(cls, properties: dict, names: PropertyNames, new_status: str) -> "Schema":
        problems, types = [], {}
        for field, allowed in _EXPECTED.items():
            prop_name = getattr(names, field)
            prop = properties.get(prop_name)
            if prop is None:
                problems.append(f'missing property "{prop_name}" (expected {" or ".join(sorted(allowed))})')
            elif prop["type"] not in allowed:
                problems.append(f'"{prop_name}" is {prop["type"]}, expected {" or ".join(sorted(allowed))}')
            else:
                types[field] = prop["type"]
        if types.get("status") == "status":
            options = [o["name"] for o in properties[names.status]["status"]["options"]]
            if new_status not in options:
                # Unlike select options, status options can't be created through the API.
                problems.append(f'Status has no "{new_status}" option (has: {", ".join(options)})')
        if problems:
            raise SchemaError("Notion database doesn't match config.toml:\n  - " + "\n  - ".join(problems))
        return cls(names, types)

    def encode(self, values: Values, *, source_id: str | None = None,
               status: str | None = None) -> dict:
        """Notion property payload for the given logical field values."""
        n, props = self.names, {}
        for name, value in values.items():
            prop = getattr(n, name)
            if name == "title":
                props[prop] = {"title": _rich_text(value or "Untitled")}
            elif name in ("course", "type"):
                if self.types[name] == "select":
                    # Select option names can't contain commas.
                    props[prop] = {"select": {"name": value.replace(",", " ")} if value else None}
                else:
                    props[prop] = {"rich_text": _rich_text(value or "")}
            elif name == "due":
                props[prop] = {"date": {"start": value} if value else None}
            elif name == "link":
                props[prop] = {"url": value or None}
        if source_id is not None:
            props[n.source_id] = {"rich_text": _rich_text(source_id)}
        if status is not None:
            props[n.status] = {self.types["status"]: {"name": status}}
        return props

    def decode(self, page: dict) -> ExistingPage:
        """The logical fields of an existing row. Rows added by hand have Source ID ""."""
        props, n = page["properties"], self.names
        return ExistingPage(page_id=page["id"], source_id=_plain(props[n.source_id].get("rich_text")), values={
            "title": _plain(props[n.title].get("title")),
            "course": _select_or_text(props[n.course]),
            "type": _select_or_text(props[n.type]),
            "due": (props[n.due].get("date") or {}).get("start"),
            "link": props[n.link].get("url"),
        })


def _rich_text(value: str) -> list[dict]:
    return [{"type": "text", "text": {"content": value[:_TEXT_LIMIT]}}] if value else []


def _plain(rich: list[dict] | None) -> str:
    return "".join(r.get("plain_text", "") for r in rich or []).strip()


def _select_or_text(prop: dict) -> str | None:
    if prop["type"] == "select":
        return (prop.get("select") or {}).get("name")
    return _plain(prop.get("rich_text")) or None

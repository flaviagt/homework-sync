"""One sync run: collect from every source, plan against Notion, apply."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import requests

from . import canvas
from .config import Config, Secrets
from .dedup import drop_site_duplicates_of_canvas, unique_by_source_id
from .models import Item
from .notion import NotionClient, NotionError, Schema
from .planner import ExistingPage, Plan, fingerprint, source_values
from .sites import SiteError, collect_site
from .sites.llm import ClaudeExtractor
from .state import SiteSnapshot, SyncState, item_to_dict, opaque

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    name: str
    count: int = 0
    method: str = ""
    new_since_last_run: int | None = None
    error: str | None = None


@dataclass
class Collected:
    items: list[Item] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    snapshots: dict[str, SiteSnapshot] = field(default_factory=dict)
    dropped_as_canvas_duplicates: list[Item] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def collect(config: Config, secrets: Secrets, state: SyncState, today: date,
            session: requests.Session, only: set[str] | None = None) -> Collected:
    out = Collected()
    canvas_items: list[Item] = []

    if only is None or "canvas" in only:
        report = SourceReport("canvas", method="ics")
        if not secrets.canvas_ics_url:
            report.error = "CANVAS_ICS_URL is not set"
        else:
            try:
                canvas_items = canvas.parse_ics(canvas.fetch_ics(secrets.canvas_ics_url, session), config)
                report.count = len(canvas_items)
            except Exception as e:  # noqa: BLE001 — one broken source shouldn't stop the others
                report.error = str(e)
        out.reports.append(report)

    llm = ClaudeExtractor(_anthropic_client(secrets.anthropic_api_key)) if secrets.anthropic_api_key else None
    site_items: list[Item] = []
    for site in config.sites:
        if only is not None and site.key not in only:
            continue
        report = SourceReport(site.key)
        previous = state.sites.get(site.key)
        try:
            result = collect_site(site, today=today, session=session, previous=previous, llm=llm)
        except SiteError as e:
            report.error = str(e)
        else:
            report.count, report.method = len(result.items), result.method
            if previous is not None:
                report.new_since_last_run = len({i.source_id for i in result.items} - previous.source_ids())
            out.snapshots[site.key] = SiteSnapshot(
                site.key, result.page_hash, result.method, [item_to_dict(i) for i in result.items])
            site_items += result.items
        out.reports.append(report)

    site_items, out.dropped_as_canvas_duplicates = drop_site_duplicates_of_canvas(canvas_items, site_items)
    out.items, dupes = unique_by_source_id(canvas_items + site_items)
    for item in dupes:
        log.warning("Duplicate Source ID %s from %s; keeping the first.", item.source_id, item.source)
    out.errors = [f"{r.name}: {r.error}" for r in out.reports if r.error]
    return out


def _anthropic_client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


@dataclass
class NotionTarget:
    client: NotionClient
    data_source_id: str
    schema: Schema

    @classmethod
    def connect(cls, secrets: Secrets, config: Config) -> "NotionTarget":
        client = NotionClient(secrets.notion_token)
        ds = client.resolve_data_source(secrets.notion_database_id)
        schema = Schema.validate(client.properties(ds), config.properties, config.new_status)
        return cls(client, ds, schema)

    def existing_pages(self) -> list[ExistingPage]:
        # All rows, including ones added by hand, so the planner can avoid duplicating them.
        return [self.schema.decode(p) for p in self.client.query_all(self.data_source_id)]


@dataclass
class Applied:
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def apply(plan: Plan, target: NotionTarget, state: SyncState, config: Config,
          describe=lambda item: item.title) -> Applied:
    """Write the plan to Notion, recording what was synced in ``state``.

    A failed write is reported and skipped; it isn't recorded, so the next run retries it.
    """
    done = Applied()
    for create in plan.creates:
        item, values = create.item, source_values(create.item)
        try:
            target.client.create_page(target.data_source_id, target.schema.encode(
                values, source_id=item.source_id, status=config.new_status))
        except (NotionError, requests.RequestException) as e:
            done.errors.append(f"create {describe(item)}: {e}")
            continue
        state.synced[opaque(item.source_id)] = fingerprint(values)
        done.created += 1

    for update in plan.updates:
        item, values = update.item, source_values(update.item)
        try:
            # Only the changed fields are sent; Status is never among them.
            target.client.update_page(update.page_id, target.schema.encode(
                {name: values[name] for name in update.changes}))
        except (NotionError, requests.RequestException) as e:
            done.errors.append(f"update {describe(item)}: {e}")
            continue
        state.synced[opaque(item.source_id)] = fingerprint(values)
        done.updated += 1

    for item in plan.unchanged:
        state.synced[opaque(item.source_id)] = fingerprint(source_values(item))
    return done

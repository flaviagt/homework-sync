"""Command-line entry point: ``python -m homework_sync [--dry-run]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from .config import load_config, load_secrets
from .dates import today_local
from .models import Item
from .notion import NotionError
from .planner import Plan, plan_sync
from .state import SyncState
from .sync import NotionTarget, apply, collect

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("homework_sync")


class RedactSecrets(logging.Filter):
    """Belt and braces: scrub secret values from every log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "***")
        record.msg, record.args = message, ()
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="homework_sync",
        description="Sync homework and readings from Canvas and course websites into Notion.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change without writing to Notion or the state file")
    parser.add_argument("--only", metavar="SOURCES",
                        help="comma-separated subset of sources, e.g. canvas,cse151b")
    parser.add_argument("--env-file", default=str(ROOT / "secrets.env"),
                        help="local file with secrets (default: secrets.env; ignored if missing)")
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    parser.add_argument("--state", default=str(ROOT / "state" / "sync_state.json"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # keep report and log lines in order

    if Path(args.env_file).exists():
        load_dotenv(args.env_file, override=False)  # real environment variables win
    secrets = load_secrets()
    handler = logging.StreamHandler()
    handler.addFilter(RedactSecrets(secrets.values()))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s", handlers=[handler])
    # Canvas titles stay out of public GitHub Actions logs.
    redact_private = os.environ.get("GITHUB_ACTIONS") == "true"

    def describe(item: Item) -> str:
        title = "(Canvas item)" if redact_private and item.private else item.title
        due = f" — due {item.due:%a %b %d}" if item.due else ""
        return f"[{item.course}] {item.type.value}: {title}{due}"

    config = load_config(args.config)
    state_path = Path(args.state)
    state = SyncState.load(state_path)
    today = today_local()
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    collected = collect(config, secrets, state, today, requests.Session(), only)
    print("Sources")
    for r in collected.reports:
        detail = f"ERROR: {r.error}" if r.error else f"{r.count} items via {r.method}"
        if r.new_since_last_run:
            detail += f", {r.new_since_last_run} new since last run"
        print(f"  {r.name:<10} {detail}")
    for item in collected.dropped_as_canvas_duplicates:
        print(f"  skipped (already in Canvas): {describe(item)}")

    errors = list(collected.errors)
    target = None
    if secrets.notion_token and secrets.notion_database_id:
        try:
            target = NotionTarget.connect(secrets, config)
            existing, synced = target.existing_pages(), state.synced
        except (NotionError, requests.RequestException) as e:
            log.error("%s", e)
            return 1
    elif args.dry_run:
        log.warning("NOTION_TOKEN / NOTION_DATABASE_ID not set: showing every item as new.")
        existing, synced = [], {}
    else:
        log.error("NOTION_TOKEN and NOTION_DATABASE_ID must be set (or use --dry-run).")
        return 1

    plan = plan_sync(collected.items, existing, synced, today, config.backfill_days)
    _print_plan(plan, describe, config.backfill_days, dry_run=args.dry_run)

    if not args.dry_run:
        applied = apply(plan, target, state, config, describe)
        errors += applied.errors
        state.sites.update(collected.snapshots)
        state.save(state_path)
        print(f"\nDone: {applied.created} created, {applied.updated} updated.")

    for error in errors:
        log.error("%s", error)
    _write_step_summary(plan, collected.reports, errors, args.dry_run)
    return 1 if errors else 0


def _print_plan(plan: Plan, describe, backfill_days: int, dry_run: bool) -> None:
    verb = "Would" if dry_run else "Will"
    print(f"\nNotion: {verb.lower()} create {len(plan.creates)}, update {len(plan.updates)}; "
          f"{len(plan.unchanged)} already up to date")
    for c in plan.creates:
        print(f"  + {describe(c.item)}")
    for u in plan.updates:
        fields = ", ".join(f"{name}: {old!r} → {new!r}" for name, (old, new) in u.changes.items())
        print(f"  ~ {describe(u.item)}\n      {fields}")
    if plan.skipped_old:
        print(f"  ({len(plan.skipped_old)} items due more than {backfill_days} days ago not imported)")
    for item, page in plan.skipped_similar:
        print(f"  (already in Notion as {page.values.get('title')!r}: {describe(item)})")
    for item in plan.skipped_deleted:
        print(f"  (not re-creating, deleted in Notion: {describe(item)})")
    for page in plan.duplicate_pages:
        print(f"  (warning: more than one Notion row has Source ID {page.source_id}; left alone)")


def _write_step_summary(plan: Plan, reports, errors: list[str], dry_run: bool) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Homework sync{' (dry run)' if dry_run else ''}", "",
             "| Source | Items | Status |", "|---|---|---|"]
    lines += [f"| {r.name} | {r.count} | {'❌ ' + r.error if r.error else '✅ ' + r.method} |"
              for r in reports]
    lines += ["", f"**Notion:** {len(plan.creates)} created, {len(plan.updates)} updated, "
              f"{len(plan.unchanged)} unchanged."]
    if errors:
        lines += ["", f"**{len(errors)} error(s)** — see the log."]
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())

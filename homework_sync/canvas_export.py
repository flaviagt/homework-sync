"""Export Canvas assignments to a CSV: ``python -m homework_sync.canvas_export``.

Reads the same calendar feed as the Notion sync (CANVAS_ICS_URL). The feed has
no submission status, release dates or late deadlines, so those columns are blank.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import canvas
from .config import load_config, load_secrets
from .export_csv import as_datetime, row, sort_rows, write_and_print

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="homework_sync.canvas_export",
        description="Write your Canvas assignments and due dates to a CSV file.")
    parser.add_argument("--out", default=str(ROOT / "output" / "canvas_assignments.csv"))
    parser.add_argument("--env-file", default=str(ROOT / "secrets.env"))
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    args = parser.parse_args(argv)

    if Path(args.env_file).exists():
        load_dotenv(args.env_file, override=False)
    url = load_secrets().canvas_ics_url
    if not url:
        print("Set CANVAS_ICS_URL in secrets.env (Canvas → Calendar → Calendar Feed).", file=sys.stderr)
        return 1

    config = load_config(args.config)
    try:
        items = canvas.parse_ics(canvas.fetch_ics(url), config)
    except canvas.CanvasFetchError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    rows = sort_rows([row(i.course, i.title, as_datetime(i.due), i.link or "") for i in items])
    write_and_print(rows, Path(args.out), "Canvas")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Export Gradescope assignments to a CSV: ``python -m homework_sync.gradescope_export``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_config
from .export_csv import row, sort_rows, write_and_print
from .gradescope import Assignment, GradescopeClient, GradescopeError, parse_courses

ROOT = Path(__file__).resolve().parents[1]


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip().strip("'\"").strip()
        if value:
            return value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="homework_sync.gradescope_export",
        description="Write your Gradescope assignments and due dates to a CSV file.")
    parser.add_argument("--courses", help="comma-separated Gradescope course codes "
                        "(default: [gradescope] courses in config.toml)")
    parser.add_argument("--out", default=str(ROOT / "output" / "gradescope_assignments.csv"))
    parser.add_argument("--env-file", default=str(ROOT / "secrets.env"))
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    args = parser.parse_args(argv)

    if Path(args.env_file).exists():
        load_dotenv(args.env_file, override=False)
    email, password = _env("GRADESCOPE_EMAIL"), _env("GRADESCOPE_PASS", "GRADESCOPE_PASSWORD")
    if not email or not password:
        print("Set GRADESCOPE_EMAIL and GRADESCOPE_PASS in secrets.env.", file=sys.stderr)
        return 1

    config = load_config(args.config)
    wanted = ([c.strip() for c in args.courses.split(",")] if args.courses
              else config.gradescope_courses)

    client = GradescopeClient()
    try:
        courses = parse_courses(client.login(email, password))
        if wanted:
            selected = [c for c in courses if c.shortname in wanted]
            missing = set(wanted) - {c.shortname for c in selected}
            if missing:
                print(f"Not found on your Gradescope account: {', '.join(sorted(missing))}", file=sys.stderr)
        else:  # nothing configured: the most recent term
            selected = [c for c in courses if courses and c.term == courses[0].term]
        assignments: list[Assignment] = []
        for course in selected:
            assignments += client.assignments(course)
    except GradescopeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    rows = sort_rows([
        row(config.course_name(a.course.shortname), a.title, a.due, a.url,
            status=a.status, late_due=a.late_due, released=a.released)
        for a in assignments])
    write_and_print(rows, Path(args.out), ", ".join(c.shortname for c in selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())

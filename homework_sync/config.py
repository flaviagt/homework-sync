"""Load config.toml and secrets from the environment."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PropertyNames:
    title: str = "What"
    course: str = "Course"
    type: str = "Type"
    due: str = "Due"
    link: str = "Link"
    status: str = "Status"
    source_id: str = "Source ID"


@dataclass(frozen=True)
class SiteConfig:
    key: str
    course: str
    url: str
    parser: str


@dataclass(frozen=True)
class Config:
    backfill_days: int = 7
    courses: dict[str, str] = field(default_factory=dict)
    sites: list[SiteConfig] = field(default_factory=list)
    properties: PropertyNames = PropertyNames()
    new_status: str = "Not started"
    gradescope_courses: list[str] = field(default_factory=list)

    def course_name(self, code: str) -> str:
        """Display name for a Canvas course code like ``MGT016_FA26_005``."""
        matches = [p for p in self.courses if code.upper().startswith(p.upper())]
        if matches:
            return self.courses[max(matches, key=len)]
        m = re.match(r"([A-Z]+)0*(\d+[A-Z]*)", code.upper())
        return f"{m.group(1)} {m.group(2)}" if m else code


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    notion = raw.get("notion", {})
    return Config(
        backfill_days=int(raw.get("sync", {}).get("backfill_days", 7)),
        courses=dict(raw.get("courses", {})),
        sites=[SiteConfig(**s) for s in raw.get("sites", [])],
        properties=PropertyNames(**notion.get("properties", {})),
        new_status=notion.get("new_status", "Not started"),
        gradescope_courses=list(raw.get("gradescope", {}).get("courses", [])),
    )


@dataclass(frozen=True)
class Secrets:
    canvas_ics_url: str | None
    notion_token: str | None
    notion_database_id: str | None
    anthropic_api_key: str | None

    def values(self) -> list[str]:
        """Every secret value, for log redaction."""
        return [v for v in vars(self).values() if v]


def _env(name: str) -> str | None:
    # Tolerate values pasted with surrounding quotes or whitespace; treat empty
    # as unset (GitHub passes "" for secrets that don't exist).
    value = os.environ.get(name, "").strip().strip("'\"").strip()
    return value or None


def load_secrets() -> Secrets:
    return Secrets(
        canvas_ics_url=_env("CANVAS_ICS_URL"),
        notion_token=_env("NOTION_TOKEN"),
        notion_database_id=_env("NOTION_DATABASE_ID"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
    )

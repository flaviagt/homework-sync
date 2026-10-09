from datetime import date
from pathlib import Path

import pytest

from homework_sync.config import load_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 27)


@pytest.fixture
def config():
    return load_config(ROOT / "config.toml")


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def site(config):
    return lambda key: next(s for s in config.sites if s.key == key)

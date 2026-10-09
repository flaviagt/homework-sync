from datetime import date, datetime

import pytest

from homework_sync import canvas
from homework_sync.dates import LA
from homework_sync.models import ItemType

from .conftest import FIXTURES


@pytest.fixture
def items(config):
    by_uid = {i.source_id: i for i in canvas.parse_ics((FIXTURES / "canvas_sample.ics").read_bytes(), config)}
    return by_uid


def test_title_and_course_are_split(items):
    hw = items["event-assignment-1001"]
    assert hw.title == "Homework #1: Satellite imagery and poverty prediction"
    assert hw.course == "DSC 190"


def test_kind_prefix_and_escaped_commas_are_cleaned(items):
    assert items["event-assignment-1002"].title == "Ethics Simulation Advance Preparation"
    assert items["event-assignment-1003"].title == "Quiz #1 (Ethics Definitions, Ethical GPS)"


def test_uid_is_the_source_id_and_link_points_at_the_assignment(items):
    hw = items["event-assignment-1001"]
    assert hw.link == "https://canvas.example.edu/courses/42/assignments/1001"
    assert hw.private  # kept out of public CI logs


def test_all_day_and_timed_due_dates(items):
    assert items["event-assignment-1001"].due == date(2026, 10, 13)
    timed = items["event-assignment-1002"].due
    assert isinstance(timed, datetime)
    assert timed == datetime(2026, 11, 16, 12, 30, tzinfo=LA)  # 20:30 UTC is 12:30 PST


def test_types(items):
    assert items["event-assignment-1003"].type == ItemType.QUIZ
    assert items["event-assignment-1004"].type == ItemType.HOMEWORK  # "Midterm report" isn't an exam
    assert items["event-calendar-event-2002"].type == ItemType.TEST


def test_lectures_are_skipped(items):
    assert "event-calendar-event-2001" not in items


def test_unknown_course_code_gets_a_readable_name(config):
    assert config.course_name("COGS108_FA26_A00") == "COGS 108"
    assert config.course_name("DSC180A_FA26_A00") == "DSC Capstone"


def test_fetch_error_never_contains_the_feed_url():
    import requests

    secret_url = "https://canvas.example.edu/feeds/calendars/user_SECRETTOKEN.ics"

    class Session:
        def get(self, url, **kwargs):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    with pytest.raises(canvas.CanvasFetchError) as err:
        canvas.fetch_ics(secret_url, Session())
    assert "SECRETTOKEN" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__

from datetime import date

import pytest

from homework_sync.models import ItemType
from homework_sync.sites import SiteError, SiteResult, collect_site, capstone, cse151b
from homework_sync.sites.base import LayoutChanged, page_hash, page_text
from homework_sync.state import SiteSnapshot, item_to_dict

from .conftest import TODAY


def by_title(items):
    return {i.title: i for i in items}


# --- CSE 151B -------------------------------------------------------------------------

@pytest.fixture
def cse(fixture_text, site):
    return by_title(cse151b.parse(fixture_text("cse151b.html"), site("cse151b"), TODAY))


def test_cse151b_combined_assignments_are_split(cse):
    assert cse["HW1"].due == cse["PA1"].due == date(2026, 10, 5)
    assert cse["HW2"].link == "https://colab.research.google.com/drive/pa2"


def test_cse151b_ungraded_and_links(cse):
    assert cse["HW0 (ungraded)"].due is None
    assert cse["PA0 (ungraded)"].link == "https://colab.research.google.com/drive/pa0"
    # No link on the page → the course page itself.
    assert cse["HW1"].link == "https://sadhikamalladi.github.io/cs151b-fa26/index.html"


def test_cse151b_quizzes_readings_milestones_and_exams(cse):
    assert cse["Quiz 1"].type == ItemType.QUIZ and cse["Quiz 1"].due == date(2026, 10, 5)
    reading = cse["Reading: Goodfellow ch. 6"]
    assert reading.type == ItemType.READING
    assert reading.due == date(2026, 10, 6)  # the lecture it's for
    assert cse["Project proposal"].due == date(2026, 10, 16)
    assert cse["Midterm"].type == ItemType.TEST and cse["Midterm"].due == date(2026, 10, 20)


def test_cse151b_skips_non_items(cse):
    assert "Foundations clinic" not in " ".join(cse)  # a discussion, not a quiz
    assert "Midterm review" not in cse                 # a lecture, not an exam
    assert all(i.course == "CSE 151B" for i in cse.values())


def test_cse151b_ids_survive_a_link_being_added(fixture_text, site):
    html = fixture_text("cse151b.html")
    before = by_title(cse151b.parse(html, site("cse151b"), TODAY))["HW1"]
    html = html.replace('<span class="event">HW1 &amp; PA1</span>',
                        '<span class="event">HW1 (<a href="https://example.edu/hw1.pdf">PDF</a>) &amp; PA1</span>')
    after = by_title(cse151b.parse(html, site("cse151b"), TODAY))["HW1"]
    assert after.link == "https://example.edu/hw1.pdf"
    assert after.source_id == before.source_id


def test_cse151b_layout_change_is_detected(site):
    with pytest.raises(LayoutChanged):
        cse151b.parse("<html><body><p>Schedule coming soon</p></body></html>", site("cse151b"), TODAY)


# --- DSC Capstone ---------------------------------------------------------------------

@pytest.fixture
def cap(fixture_text, site):
    return by_title(capstone.parse(fixture_text("capstone.html"), site("capstone"), TODAY))


def test_capstone_readings_are_due_at_the_weeks_session(cap):
    syllabus = cap["Read the Syllabus"]
    assert syllabus.type == ItemType.READING and syllabus.due == date(2026, 10, 5)
    lesson = cap["AWS Fundamentals: S3 and EC2"]
    assert lesson.type == ItemType.READING
    assert lesson.link == "https://dsc-capstone.org/2026-27/lessons/01a"  # relative link resolved


def test_capstone_assignment_due_dates(cap):
    # No date given: the site's rule is "due a week after release".
    assert cap["Methodology Assignment 1 (Gradescope)"].due == date(2026, 10, 19)
    # Explicit dates win.
    assert cap["Participation Assignment 1 due Sunday, Oct 18"].due == date(2026, 10, 18)
    assert cap["Checkpoint 1 due Oct 26"].due == date(2026, 10, 26)


def test_capstone_skips_live_sessions(cap):
    assert not any(t.startswith("Live") or t in ("Introduction", "Reproducibility") for t in cap)


# --- Fallback to Claude ---------------------------------------------------------------

class FakeSession:
    def __init__(self, html):
        self.html = html

    def get(self, url, **kwargs):
        resp = type("Resp", (), {})()
        resp.text, resp.raise_for_status = self.html, lambda: None
        return resp


class FakeLLM:
    def __init__(self, items=()):
        self.items, self.calls = list(items), 0

    def extract(self, text, site, today):
        self.calls += 1
        return self.items


BROKEN = "<html><body><h2>Week 1</h2><p>HW1 due Oct 5</p></body></html>"


def test_layout_change_without_api_key_is_an_error(site):
    with pytest.raises(SiteError, match="ANTHROPIC_API_KEY"):
        collect_site(site("cse151b"), today=TODAY, session=FakeSession(BROKEN), previous=None, llm=None)


def test_layout_change_uses_claude(site, cap):
    llm = FakeLLM(list(cap.values())[:1])
    result = collect_site(site("capstone"), today=TODAY, session=FakeSession(BROKEN), previous=None, llm=llm)
    assert (result.method, llm.calls, len(result.items)) == ("claude", 1, 1)


def test_unchanged_page_reuses_previous_claude_result(site, cap):
    s = site("capstone")
    cached_item = next(iter(cap.values()))
    previous = SiteSnapshot("capstone", page_hash(page_text(BROKEN, s.url)), "claude",
                            [item_to_dict(cached_item)])
    llm = FakeLLM()
    result = collect_site(s, today=TODAY, session=FakeSession(BROKEN), previous=previous, llm=llm)
    assert result.method == "claude-cached" and llm.calls == 0
    assert result.items[0].source_id == cached_item.source_id


def test_parser_result_is_used_when_layout_is_fine(site, fixture_text):
    llm = FakeLLM()
    result = collect_site(site("capstone"), today=TODAY, session=FakeSession(fixture_text("capstone.html")),
                          previous=None, llm=llm)
    assert isinstance(result, SiteResult) and result.method == "parser" and llm.calls == 0

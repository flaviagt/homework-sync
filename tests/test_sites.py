from datetime import date

import pytest

from homework_sync.models import ItemType
from homework_sync.sites import SiteError, SiteResult, collect_site, capstone
from homework_sync.sites.base import LayoutChanged, page_hash, page_text
from homework_sync.state import SiteSnapshot, item_to_dict

from .conftest import TODAY


def by_title(items):
    return {i.title: i for i in items}


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


def test_capstone_layout_change_is_detected(site):
    with pytest.raises(LayoutChanged):
        capstone.parse("<html><body><p>Schedule coming soon</p></body></html>", site("capstone"), TODAY)


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
        collect_site(site("capstone"), today=TODAY, session=FakeSession(BROKEN), previous=None, llm=None)


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


LIVE_LAYOUT = """<table><thead><tr><th>Week<th colspan="2">Methodology<th>Domain<th>Project<tbody>
<tr><td>4<td><a href="lessons/04">Effective Communication</a><br />featuring Prof. Smarr<br /><a href="https://gs/ma2">MA 2 (due Oct. 26th)</a><td><td><td>
<tr><td>5<td><a href="lessons/05">Using Git</a><br /><a href="https://gs/ma3">MA 3 (due Nov. 2nd)</a><td><td>As before, Participation questions are due Sunday<td>
<tr><td>6<td><a href="lessons/06">Docker</a><td><td><td>
</table>"""


def test_capstone_dates_weeks_from_their_assignments(site):
    items = by_title(capstone.parse(LIVE_LAYOUT, site("capstone"), date(2026, 11, 1)))
    assert items["MA 2 (due Oct. 26th)"].due == date(2026, 10, 26)        # "Oct. 26th" is read
    assert items["Effective Communication"].due == date(2026, 10, 19)     # released a week earlier
    assert items["Using Git"].due == date(2026, 10, 26)
    assert items["Docker"].due == date(2026, 11, 2)                       # undated week, counted on
    assert not any(t.startswith(("As before", "featuring")) for t in items)

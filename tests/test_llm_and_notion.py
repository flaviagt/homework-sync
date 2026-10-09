"""Claude fallback response handling and Notion schema/retry handling, with fakes (no network)."""

from datetime import date
from types import SimpleNamespace

import pytest

from homework_sync.config import PropertyNames
from homework_sync.models import ItemType
from homework_sync.notion import NotionClient, Schema, SchemaError
from homework_sync.sites.llm import ClaudeExtractor, Extraction, ExtractedItem, LLMExtractionError

from .conftest import TODAY


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(stop_reason="end_turn", items=()):
    parsed = Extraction(items=list(items)) if stop_reason == "end_turn" else None
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed))
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_claude_output_becomes_items(site):
    client, messages = fake_client(items=[
        ExtractedItem(title="HW3", type="homework", due_date="2026-10-26", release_date=None,
                      source_url="https://example.edu/hw3"),
        ExtractedItem(title="Read chapter 4", type="reading", due_date="not a date",
                      release_date=None, source_url="javascript:alert(1)"),
    ])
    items = ClaudeExtractor(client, model="claude-opus-5").extract("page text", site("cse151b"), TODAY)

    hw, reading = items
    assert (hw.type, hw.due, hw.link) == (ItemType.HOMEWORK, date(2026, 10, 26), "https://example.edu/hw3")
    assert reading.type == ItemType.READING
    assert reading.due is None  # invalid dates are dropped, not guessed
    assert reading.link == site("cse151b").url  # non-http links are ignored
    assert messages.kwargs["output_format"] is Extraction
    assert messages.kwargs["fallbacks"] == "default"


def test_refusal_raises(site):
    client, _ = fake_client(stop_reason="refusal")
    with pytest.raises(LLMExtractionError):
        ClaudeExtractor(client, model="claude-opus-5").extract("text", site("cse151b"), TODAY)


def test_fallbacks_only_sent_for_models_that_support_them(site):
    client, messages = fake_client()
    ClaudeExtractor(client, model="claude-haiku-4-5").extract("text", site("cse151b"), TODAY)
    assert "fallbacks" not in messages.kwargs


# --- Notion -----------------------------------------------------------------------------

def notion_properties(**overrides):
    props = {
        "What": {"type": "title"}, "Course": {"type": "select"}, "Type": {"type": "select"},
        "Due": {"type": "date"}, "Link": {"type": "url"}, "Source ID": {"type": "rich_text"},
        "Status": {"type": "status", "status": {"options": [{"name": "Not started"}, {"name": "Started"}]}},
    }
    props.update(overrides)
    return {k: v for k, v in props.items() if v is not None}


def test_schema_matching_the_real_database_validates():
    schema = Schema.validate(notion_properties(), PropertyNames(), "Not started")
    assert schema.types["status"] == "status"


def test_schema_problems_are_all_reported():
    props = notion_properties(**{"Source ID": None, "Due": {"type": "rich_text"}})
    with pytest.raises(SchemaError) as err:
        Schema.validate(props, PropertyNames(), "To do")
    message = str(err.value)
    assert 'missing property "Source ID"' in message
    assert '"Due" is rich_text' in message
    assert 'no "To do" option' in message


def test_decode_reads_rows_added_by_hand():
    schema = Schema.validate(notion_properties(), PropertyNames(), "Not started")
    manual_row = {"id": "p", "properties": {
        "What": {"type": "title", "title": [{"plain_text": "HW1"}]},
        "Course": {"type": "select", "select": {"name": "CSE 151B"}},
        "Type": {"type": "select", "select": None},
        "Due": {"type": "date", "date": {"start": "2026-10-05"}},
        "Link": {"type": "url", "url": None},
        "Source ID": {"type": "rich_text", "rich_text": []},
    }}
    page = schema.decode(manual_row)
    assert page.source_id == ""
    assert page.values == {"title": "HW1", "course": "CSE 151B", "type": None,
                           "due": "2026-10-05", "link": None}


def test_client_retries_rate_limits():
    responses = [SimpleNamespace(status_code=429, ok=False, headers={"Retry-After": "1"}),
                 SimpleNamespace(status_code=200, ok=True, headers={}, json=lambda: {"ok": True})]

    class Session:
        headers = {}

        def request(self, *args, **kwargs):
            return responses.pop(0)

    slept = []
    client = NotionClient("token", session=Session(), sleep=slept.append)
    assert client.request("GET", "/users/me") == {"ok": True}
    assert slept == [1.0]

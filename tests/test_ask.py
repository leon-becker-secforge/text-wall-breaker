from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from pretty_prompt import ask as ask_module
from pretty_prompt.ask import (
    AskRequest,
    BrowserUnavailable,
    InvalidAsk,
    Option,
    Question,
    _resolve_accent,
    run_ask,
)


def test_resolve_accent_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("PRETTY_PROMPT_ACCENT", raising=False)
    assert _resolve_accent() is None


def test_resolve_accent_returns_a_valid_value(monkeypatch):
    monkeypatch.setenv("PRETTY_PROMPT_ACCENT", "#2563eb")
    assert _resolve_accent() == "#2563eb"


def test_resolve_accent_falls_back_to_none_when_invalid(monkeypatch):
    # server.main() is the gate that refuses to start over a bad value — this is the
    # fallback for run_ask being called directly (as the rest of this file does),
    # bypassing that gate.
    monkeypatch.setenv("PRETTY_PROMPT_ACCENT", "not-a-colour")
    assert _resolve_accent() is None


def test_empty_options_without_free_text_is_rejected():
    request = AskRequest(question="q", options=[], allow_free_text=False)
    with pytest.raises(InvalidAsk, match="no way to answer"):
        request.validated()


def test_empty_options_with_free_text_is_allowed():
    AskRequest(question="q", options=[], allow_free_text=True).validated()


def test_duplicate_option_ids_are_rejected():
    request = AskRequest(
        question="q",
        options=[Option(id="a", label="A"), Option(id="a", label="B")],
    )
    with pytest.raises(InvalidAsk, match="duplicate"):
        request.validated()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"question": "  "}, "question is empty"),
        ({"question": "q", "options": [Option(id=" ", label="A")]}, "non-empty id"),
        ({"question": "q", "options": [Option(id="a", label=" ")]}, "empty label"),
        ({"question": "q", "timeout_seconds": 0}, "must be positive"),
    ],
)
def test_rejected_requests(kwargs, match):
    with pytest.raises(InvalidAsk, match=match):
        AskRequest(**kwargs).validated()


class FakeBrowser:
    """Stands in for xdg-open: reads the page and answers it from another thread."""

    def __init__(self, respond):
        self.respond = respond
        self.page = ""
        self.path = None
        self.thread: threading.Thread | None = None

    def __call__(self, path):
        self.path = path
        self.page = path.read_text(encoding="utf-8")
        url = self.page.split('var SUBMIT_URL = "')[1].split('"')[0]
        payload = self.respond(self.page)
        if payload is None:
            return
        self.thread = threading.Thread(target=lambda: _post(url, payload))
        self.thread.start()


def _post(url: str, payload: object) -> None:
    request = urllib.request.Request(  # noqa: S310 — loopback URL from our own page
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=5)  # noqa: S310 — loopback URL from our own page


@pytest.fixture
def browser(monkeypatch):
    def install(respond):
        fake = FakeBrowser(respond)
        monkeypatch.setattr(ask_module, "open_in_browser", fake)
        return fake

    return install


def test_submission_returns_ids_and_free_text(browser):
    browser(lambda page: {"kind": "submit", "data": {"selected": ["b"], "free_text": " hi "}})
    result = run_ask(
        AskRequest(question="q", options=[Option(id="a", label="A"), Option(id="b", label="B")])
    )
    assert result == {"selected": ["b"], "free_text": "hi"}


def test_cancel_is_reported_as_cancelled(browser):
    browser(lambda page: {"kind": "cancel", "data": {}})
    assert run_ask(AskRequest(question="q", options=[Option(id="a", label="A")])) == {
        "cancelled": True
    }


def test_no_answer_times_out(browser):
    browser(lambda page: None)
    result = run_ask(AskRequest(question="q", timeout_seconds=0.05))
    assert result == {"timed_out": True}


def test_unknown_ids_in_a_submission_are_dropped(browser):
    browser(lambda page: {"kind": "submit", "data": {"selected": ["a", "nope"]}})
    result = run_ask(AskRequest(question="q", options=[Option(id="a", label="A")]))
    assert result == {"selected": ["a"], "free_text": ""}


def test_the_page_carries_the_options_and_a_matching_token(browser):
    fake = browser(lambda page: {"kind": "submit", "data": {"selected": ["a"]}})
    run_ask(
        AskRequest(
            question="# Heading\n\nbody",
            options=[Option(id="a", label="Alpha", body="**bold**")],
        )
    )
    assert "<h1>Heading</h1>" in fake.page
    assert 'data-option-id="a"' in fake.page
    assert "Alpha" in fake.page
    assert "<strong>bold</strong>" in fake.page
    # 32 hex characters from secrets.token_hex(16), baked in before the page was written.
    token = fake.page.split("/submit?t=")[1].split('"')[0]
    assert len(token) == 32


def test_multi_select_switches_the_input_type(browser):
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(question="q", options=[Option(id="a", label="A")], multi_select=True))
    assert 'type="checkbox"' in fake.page

    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(question="q", options=[Option(id="a", label="A")], multi_select=False))
    assert 'type="radio"' in fake.page


def test_free_text_box_is_omitted_when_not_allowed(browser):
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(question="q", options=[Option(id="a", label="A")], allow_free_text=False))
    assert 'id="pp-free-text"' not in fake.page
    assert "<textarea" not in fake.page.split("</form>")[0]


def test_the_temp_page_does_not_outlive_the_call(browser):
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(question="q"))
    assert fake.path is not None
    assert not fake.path.exists()
    assert not fake.path.parent.exists()


def test_a_browser_failure_surfaces_rather_than_blocking(monkeypatch):
    def refuse(path):
        raise BrowserUnavailable("no browser")

    monkeypatch.setattr(ask_module, "open_in_browser", refuse)
    with pytest.raises(BrowserUnavailable):
        run_ask(AskRequest(question="q", timeout_seconds=60))


def test_questions_and_question_together_are_rejected():
    request = AskRequest(question="q", questions=[Question(question="a")])
    with pytest.raises(InvalidAsk, match="not both"):
        request.validated()


def test_empty_questions_list_is_rejected():
    with pytest.raises(InvalidAsk, match="questions is empty"):
        AskRequest(questions=[]).validated()


def test_a_question_within_questions_is_labelled_in_the_error():
    request = AskRequest(questions=[Question(question="q"), Question(question="  ")])
    with pytest.raises(InvalidAsk, match=r"questions\[1\]: question is empty"):
        request.validated()


def test_multi_question_submission_returns_one_answer_per_question(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {
                "answers": [
                    {"selected": ["a"], "free_text": ""},
                    {"selected": [], "free_text": " second "},
                ]
            },
        }
    )
    result = run_ask(
        AskRequest(
            questions=[
                Question(question="first", options=[Option(id="a", label="A")]),
                Question(question="second", allow_free_text=True, options=[]),
            ]
        )
    )
    assert result == {
        "answers": [
            {"selected": ["a"], "free_text": ""},
            {"selected": [], "free_text": "second"},
        ]
    }


def test_multi_question_filters_unknown_ids_per_question(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"answers": [{"selected": ["a", "nope"], "free_text": ""}]},
        }
    )
    result = run_ask(
        AskRequest(questions=[Question(question="q", options=[Option(id="a", label="A")])])
    )
    assert result == {"answers": [{"selected": ["a"], "free_text": ""}]}


def test_multi_question_missing_answers_are_padded_empty(browser):
    browser(lambda page: {"kind": "submit", "data": {"answers": []}})
    result = run_ask(
        AskRequest(
            questions=[
                Question(question="first", options=[Option(id="a", label="A")]),
                Question(question="second"),
            ]
        )
    )
    assert result == {
        "answers": [
            {"selected": [], "free_text": ""},
            {"selected": [], "free_text": ""},
        ]
    }


def test_multi_question_cancel_is_reported_as_cancelled(browser):
    browser(lambda page: {"kind": "cancel", "data": {}})
    result = run_ask(AskRequest(questions=[Question(question="q")]))
    assert result == {"cancelled": True}


def test_the_page_carries_every_question(browser):
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(
        AskRequest(
            questions=[
                Question(question="# First", options=[Option(id="a", label="A")]),
                Question(question="Second", options=[Option(id="b", label="B")]),
            ]
        )
    )
    assert "<h1>First</h1>" in fake.page
    assert "Second" in fake.page
    assert 'data-option-id="a"' in fake.page
    assert 'data-option-id="b"' in fake.page

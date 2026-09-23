from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from pretty_prompt import ask as ask_module
from pretty_prompt import session as session_module
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


def test_several_recommended_options_are_allowed_with_multi_select():
    AskRequest(
        question="q",
        multi_select=True,
        options=[
            Option(id="a", label="A", recommended=True),
            Option(id="b", label="B", recommended=True),
        ],
    ).validated()


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
        (
            {
                "question": "q",
                "options": [
                    Option(id="a", label="A", recommended=True),
                    Option(id="b", label="B", recommended=True),
                ],
            },
            "2 options are recommended",
        ),
        (
            {"question": "q", "options": [Option(id="a", label="A", caveat="x" * 161)]},
            r"option 'a'.*caveat.*161.*160",
        ),
        (
            {"question": "q", "options": [Option(id="a", label="A", caveat="one\ntwo")]},
            r"option 'a'.*caveat.*one line",
        ),
    ],
)
def test_rejected_requests(kwargs, match):
    with pytest.raises(InvalidAsk, match=match):
        AskRequest(**kwargs).validated()


class FakeBrowser:
    """Stands in for xdg-open: reads the page and answers it from another thread.

    `open_in_browser` is called with a `Path` for the one-off tab, or a
    `http://127.0.0.1:...` URL string for the shared, reused-tab session (see
    `SessionListener`) — this reads either the same way the real browser would.
    """

    def __init__(self, respond):
        self.respond = respond
        self.page = ""
        self.path = None
        self.thread: threading.Thread | None = None

    def __call__(self, target):
        self.path = target
        if isinstance(target, str):
            with urllib.request.urlopen(target, timeout=5) as response:  # noqa: S310
                self.page = response.read().decode("utf-8")
        else:
            self.page = target.read_text(encoding="utf-8")
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


@pytest.fixture(autouse=True)
def _isolated_shared_session():
    """Give every test its own shared-tab session, closed after, never the real one.

    Without this, tests would fight over one process-wide singleton — an earlier test's
    claimed-but-not-released session would make a later one silently fall back to its
    one-off tab, or two tests' assertions about the shared port would collide.
    """
    original = session_module._session
    session_module._session = None
    yield
    if session_module._session is not None:
        session_module._session.close()
    session_module._session = original


@pytest.fixture
def browser(monkeypatch):
    """Installs FakeBrowser and forces the one-off tab path (no shared-session reuse).

    Pre-claiming the shared session's busy lock — and never releasing it — makes every
    `run_ask` call under this fixture fall back to its own fresh `LoopbackListener` tab,
    exactly as it always did before tab reuse existed. That is what the ~20 tests using
    this fixture are actually testing (the page's content, validation, timeouts, ...);
    reuse itself gets its own tests below, since a second `run_ask` call in a reused
    session publishes new content into the *same* already-open tab rather than calling
    `open_in_browser` again, which this fixture's one-shot FakeBrowser can't simulate.
    """

    def install(respond):
        session_module.get_shared_session().try_claim()
        fake = FakeBrowser(respond)
        monkeypatch.setattr(ask_module, "open_in_browser", fake)
        return fake

    return install


def test_submission_returns_ids_and_free_text(browser):
    browser(lambda page: {"kind": "submit", "data": {"selected": ["b"], "free_text": " hi "}})
    result = run_ask(
        AskRequest(question="q", options=[Option(id="a", label="A"), Option(id="b", label="B")])
    )
    assert result == {"selected": ["b"], "free_text": "hi", "expanded": []}


def test_submission_returns_expanded_ids(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"selected": ["b"], "free_text": "", "expanded": ["a"]},
        }
    )
    result = run_ask(
        AskRequest(question="q", options=[Option(id="a", label="A"), Option(id="b", label="B")])
    )
    assert result == {"selected": ["b"], "free_text": "", "expanded": ["a"]}


def test_unknown_expanded_ids_are_dropped(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"selected": [], "free_text": "", "expanded": ["a", "nope"]},
        }
    )
    result = run_ask(AskRequest(question="q", options=[Option(id="a", label="A")]))
    assert result == {"selected": [], "free_text": "", "expanded": ["a"]}


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
    assert result == {"selected": ["a"], "free_text": "", "expanded": []}


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
            {"selected": ["a"], "free_text": "", "expanded": []},
            {"selected": [], "free_text": "second", "expanded": []},
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
    assert result == {"answers": [{"selected": ["a"], "free_text": "", "expanded": []}]}


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
            {"selected": [], "free_text": "", "expanded": []},
            {"selected": [], "free_text": "", "expanded": []},
        ]
    }


def test_multi_question_cancel_is_reported_as_cancelled(browser):
    browser(lambda page: {"kind": "cancel", "data": {}})
    result = run_ask(AskRequest(questions=[Question(question="q")]))
    assert result == {"cancelled": True}


def test_run_ask_bakes_in_the_saved_theme(browser, monkeypatch):
    monkeypatch.setattr(ask_module, "read_saved_theme", lambda: "dark")
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(question="q"))
    assert '<html lang="en" data-theme="dark">' in fake.page


def test_run_ask_bakes_in_the_saved_theme_for_questions(browser, monkeypatch):
    monkeypatch.setattr(ask_module, "read_saved_theme", lambda: "light")
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(questions=[Question(question="q")]))
    assert '<html lang="en" data-theme="light">' in fake.page


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


def _answer_current_generation(session, expected_generation, payload, timeout=5.0):
    """Answer whatever `session` has published, once it reaches `expected_generation`.

    Stands in for the real page's own reload-and-resubmit: this test isn't running a
    browser, so the "tab" here is just this function watching the session directly and
    posting to whatever SUBMIT_URL the newly published page carries.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page, generation = session.current_page()
        if generation == expected_generation and page:
            html = page.decode("utf-8")
            submit_url = html.split('var SUBMIT_URL = "')[1].split('"')[0]
            _post(submit_url, payload)
            return
        time.sleep(0.02)
    raise AssertionError(f"generation {expected_generation} was never published")


def test_run_ask_reuses_the_tab_for_a_second_call(monkeypatch):
    session = session_module.get_shared_session()
    opened = []
    monkeypatch.setattr(ask_module, "open_in_browser", opened.append)

    thread = threading.Thread(
        target=_answer_current_generation, args=(session, 1, {"kind": "cancel", "data": {}})
    )
    thread.start()
    result = run_ask(AskRequest(question="first"))
    thread.join(timeout=5)
    assert result == {"cancelled": True}
    assert len(opened) == 1

    thread = threading.Thread(
        target=_answer_current_generation, args=(session, 2, {"kind": "cancel", "data": {}})
    )
    thread.start()
    result = run_ask(AskRequest(question="second"))
    thread.join(timeout=5)
    assert result == {"cancelled": True}
    # Still just the one open — the second question was published into the same tab.
    assert len(opened) == 1


def test_run_ask_falls_back_to_a_new_tab_when_the_shared_session_is_busy():
    session = session_module.get_shared_session()
    assert session.try_claim()  # simulate another `ask` call already using the shared tab
    try:
        fake = FakeBrowser(lambda page: {"kind": "cancel", "data": {}})
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(ask_module, "open_in_browser", fake)
            result = run_ask(AskRequest(question="q"))
    finally:
        session.release()
    assert result == {"cancelled": True}
    assert isinstance(fake.path, Path)  # a one-off file:// tab, not the shared session's

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from text_wall_breaker import ask as ask_module
from text_wall_breaker import listener as listener_module
from text_wall_breaker import session as session_module
from text_wall_breaker.ask import (
    AskRequest,
    BrowserUnavailable,
    InvalidAsk,
    Option,
    Question,
    _resolve_accent,
    run_ask,
)


def test_resolve_accent_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("TEXT_WALL_BREAKER_ACCENT", raising=False)
    assert _resolve_accent() is None


def test_resolve_accent_returns_a_valid_value(monkeypatch):
    monkeypatch.setenv("TEXT_WALL_BREAKER_ACCENT", "#2563eb")
    assert _resolve_accent() == "#2563eb"


def test_resolve_accent_falls_back_to_none_when_invalid(monkeypatch):
    # server.main() is the gate that refuses to start over a bad value — this is the
    # fallback for run_ask being called directly (as the rest of this file does),
    # bypassing that gate.
    monkeypatch.setenv("TEXT_WALL_BREAKER_ACCENT", "not-a-colour")
    assert _resolve_accent() is None


def test_empty_options_without_free_text_is_rejected():
    request = AskRequest(question="q", options=[], allow_free_text=False)
    with pytest.raises(InvalidAsk, match="no way to answer"):
        request.validated()


@pytest.mark.parametrize("fragment", ['"options": [{"id": "a"}]', '{"label": "A"}'])
def test_option_json_in_question_without_options_is_rejected(fragment):
    request = AskRequest(question=f"Pick one {fragment}", allow_free_text=True)
    with pytest.raises(InvalidAsk, match="option JSON"):
        request.validated()


def test_option_json_in_question_with_options_is_accepted():
    AskRequest(
        question='Explain the `"options":` key',
        options=[Option(id="a", label="A")],
    ).validated()


def test_question_without_options_and_without_json_is_accepted():
    AskRequest(question="Describe the options you prefer", allow_free_text=True).validated()


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
        # A list is posted in order: progress reports, then the submission or cancel.
        payloads = payload if isinstance(payload, list) else [payload]
        self.thread = threading.Thread(target=lambda: [_post(url, each) for each in payloads])
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
def _short_delivery_waits(monkeypatch):
    """Delivery checks sized for a test, not a cold browser start."""
    monkeypatch.setattr(listener_module, "DELIVERY_SECONDS", 1.0)
    monkeypatch.setattr(listener_module, "REUSE_GRACE_SECONDS", 0.5)


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
    assert result == {"selected": ["b"], "free_text": "hi", "expanded": [], "changed_from": []}


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
    assert result == {"selected": ["b"], "free_text": "", "expanded": ["a"], "changed_from": []}


def test_unknown_expanded_ids_are_dropped(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"selected": [], "free_text": "", "expanded": ["a", "nope"]},
        }
    )
    result = run_ask(AskRequest(question="q", options=[Option(id="a", label="A")]))
    assert result == {"selected": [], "free_text": "", "expanded": ["a"], "changed_from": []}


def test_cancel_is_reported_as_cancelled(browser):
    browser(lambda page: {"kind": "cancel", "data": {}})
    assert run_ask(AskRequest(question="q", options=[Option(id="a", label="A")])) == {
        "cancelled": True
    }


def test_a_page_that_never_loads_is_reported_undelivered(browser, monkeypatch):
    monkeypatch.setattr(listener_module, "DELIVERY_SECONDS", 0.05)
    browser(lambda page: None)
    result = run_ask(AskRequest(question="q"))
    assert result == ask_module.NOT_DELIVERED


def test_a_loaded_page_that_goes_silent_is_abandoned(browser, monkeypatch):
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 0.3)
    browser(lambda page: {"kind": "probe", "data": {}})
    result = run_ask(AskRequest(question="q"))
    assert result == {"abandoned": True}


def test_unknown_ids_in_a_submission_are_dropped(browser):
    browser(lambda page: {"kind": "submit", "data": {"selected": ["a", "nope"]}})
    result = run_ask(AskRequest(question="q", options=[Option(id="a", label="A")]))
    assert result == {"selected": ["a"], "free_text": "", "expanded": [], "changed_from": []}


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
        run_ask(AskRequest(question="q"))


def test_questions_and_question_together_are_rejected():
    request = AskRequest(question="q", questions=[Question(question="a")])
    with pytest.raises(InvalidAsk, match="not both"):
        request.validated()


def test_header_on_a_single_question_follows_the_question():
    request = AskRequest(question="**Q?**", header="settled").validated()
    assert request.question == "**Q?**\n\nsettled"
    assert request.header == ""


def test_the_header_reaches_the_multi_question_page(browser):
    pages = []

    def respond(page):
        pages.append(page)
        return {"kind": "cancel", "data": {}}

    browser(respond)
    run_ask(AskRequest(header="Settled: **SQLite**.", questions=[Question(question="q")]))
    assert "Settled: <strong>SQLite</strong>." in pages[0].split("<body>", 1)[1]


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
            {"selected": ["a"], "free_text": "", "expanded": [], "changed_from": [], "visits": 0},
            {
                "selected": [],
                "free_text": "second",
                "expanded": [],
                "changed_from": [],
                "visits": 0,
            },
        ]
    }


def test_a_skipped_question_is_marked_only_when_nothing_was_entered(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {
                "answers": [
                    {"selected": [], "free_text": "", "skipped": True},
                    {"selected": [], "free_text": "-", "skipped": True},
                ]
            },
        }
    )
    result = run_ask(
        AskRequest(
            questions=[
                Question(question="first", options=[Option(id="a", label="A")]),
                Question(question="second", options=[Option(id="a", label="A")]),
            ]
        )
    )
    assert result["answers"][0]["skipped"] is True
    assert result["answers"][1]["free_text"] == "-"
    assert "skipped" not in result["answers"][1]


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
    assert result == {
        "answers": [
            {"selected": ["a"], "free_text": "", "expanded": [], "changed_from": [], "visits": 0}
        ]
    }


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
            {"selected": [], "free_text": "", "expanded": [], "changed_from": [], "visits": 0},
            {"selected": [], "free_text": "", "expanded": [], "changed_from": [], "visits": 0},
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
            # Loaded over HTTP, the way the tab's own reload does, so the session sees
            # the page arrive.
            with urllib.request.urlopen(session.page_url, timeout=5) as response:  # noqa: S310
                html = response.read().decode("utf-8")
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


def test_an_abandoned_ask_tells_its_tab_nobody_waits_any_more(monkeypatch):
    session = session_module.get_shared_session()
    monkeypatch.setattr(ask_module, "open_in_browser", _loading_opener([]))
    monkeypatch.setattr(listener_module, "ABANDON_SECONDS", 0.3)

    result = run_ask(AskRequest(question="q"))
    assert result == {"abandoned": True}
    status = _get_json(_session_url(session, "/status"))
    assert (status["generation"], status["open"]) == (1, False)


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


def test_a_deferred_key_in_a_payload_is_ignored(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"selected": ["a"], "free_text": "", "deferred": True},
        }
    )
    result = run_ask(AskRequest(question="q", options=[Option(id="a", label="A")]))
    assert result == {"selected": ["a"], "free_text": "", "expanded": [], "changed_from": []}


def test_changed_from_reports_abandoned_picks_only(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"selected": ["b"], "changed_from": ["a", "nope", "b", "a"]},
        }
    )
    result = run_ask(
        AskRequest(question="q", options=[Option(id="a", label="A"), Option(id="b", label="B")])
    )
    assert result["changed_from"] == ["a"]


@pytest.mark.parametrize(("raw", "expected"), [(3, 3), (-1, 0), (True, 0), ("2", 0)])
def test_visits_are_reported_per_question(browser, raw, expected):
    browser(lambda page: {"kind": "submit", "data": {"answers": [{"visits": raw}]}})
    result = run_ask(AskRequest(questions=[Question(question="q", allow_free_text=True)]))
    assert result["answers"][0]["visits"] == expected


def test_interaction_signals_can_be_switched_off(browser, monkeypatch):
    monkeypatch.setattr(ask_module, "SHARE_INTERACTION_SIGNALS", False)
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"answers": [{"selected": ["a"], "changed_from": ["a"], "visits": 2}]},
        }
    )
    result = run_ask(
        AskRequest(questions=[Question(question="q", options=[Option(id="a", label="A")])])
    )
    assert result == {"answers": [{"selected": ["a"], "free_text": "", "expanded": []}]}


def _q(question: str, option_id: str = "a") -> Question:
    return Question(question=question, options=[Option(id=option_id, label="A")])


def _session_url(session, path: str) -> str:
    return f"http://127.0.0.1:{session.port}{path}?t={session.token}"


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 (loopback)
        return json.loads(response.read())


def _loading_opener(targets: list[object]):
    """An open_in_browser that records the URL and loads it, like a tab would."""

    def open_tab(target: object) -> None:
        targets.append(target)
        urllib.request.urlopen(str(target), timeout=5).read()  # noqa: S310 (loopback)

    return open_tab


def test_a_closed_tab_gets_replaced_instead_of_published_into(monkeypatch):
    session = session_module.get_shared_session()
    opened: list[object] = []
    monkeypatch.setattr(ask_module, "open_in_browser", _loading_opener(opened))
    cancel = {"kind": "cancel", "data": {}}

    def answer_from_the_new_tab():
        # The closed tab never reloads; only the replacement one loads and answers.
        while len(opened) < 2:
            time.sleep(0.02)
        _post(session.page_url.replace("/?", "/submit?"), cancel)

    threading.Timer(0.1, _post, (session.page_url.replace("/?", "/submit?"), cancel)).start()
    assert run_ask(AskRequest(question="first")) == {"cancelled": True}
    thread = threading.Thread(target=answer_from_the_new_tab)
    thread.start()
    assert run_ask(AskRequest(question="second")) == {"cancelled": True}
    thread.join(timeout=5)
    assert len(opened) == 2


def test_a_shared_tab_that_never_loads_is_reported_undelivered(monkeypatch):
    opened: list[object] = []
    monkeypatch.setattr(ask_module, "open_in_browser", opened.append)
    assert run_ask(AskRequest(question="q")) == ask_module.NOT_DELIVERED
    assert len(opened) == 1
    # Not left claimed: the next call can use the shared tab.
    assert session_module.get_shared_session().try_claim()
    session_module.get_shared_session().release()


def test_a_question_id_is_echoed_on_its_answer_and_omitted_otherwise(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"answers": [{"selected": []}, {"selected": []}]},
        }
    )
    result = run_ask(
        AskRequest(questions=[Question(id="t180", question="a"), Question(question="b")])
    )
    first, second = result["answers"]
    assert first["id"] == "t180"
    assert "id" not in second


@pytest.mark.parametrize(
    ("ids", "match"),
    [
        (["a", "a"], r"questions\[1\]: duplicate question id 'a'"),
        (["a", "  "], r"questions\[1\]: id is empty"),
    ],
)
def test_bad_question_ids_are_rejected(ids, match):
    request = AskRequest(questions=[Question(id=i, question="q") for i in ids])
    with pytest.raises(InvalidAsk, match=match):
        request.validated()


def _checked_inputs(page: str) -> list[str]:
    return re.findall(r"<input[^>]* checked [^>]*>", page)


def _a_b(**first: Any) -> list[Option]:
    return [Option(id="a", label="A", **first), Option(id="b", label="B")]


def test_several_selected_options_need_multi_select():
    request = AskRequest(
        question="q",
        options=[
            Option(id="a", label="A", selected=True),
            Option(id="b", label="B", selected=True),
        ],
    )
    with pytest.raises(InvalidAsk, match="2 options are selected but multi_select is false"):
        request.validated()
    request.multi_select = True
    request.validated()


def _cancel_after(*reports: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *({"kind": "progress", "data": report} for report in reports),
        {"kind": "cancel", "data": {}},
    ]


def test_a_cancelled_ask_with_answers_returns_a_resume_id_that_prefills_the_page(browser):
    browser(
        lambda page: _cancel_after({"selected": ["b"], "free_text": "a <note>", "expanded": []})
    )
    request = AskRequest(question="q", options=_a_b(), multi_select=True)
    cancelled = run_ask(request)
    assert cancelled["cancelled"] is True
    assert set(cancelled) == {"cancelled", "resume"}

    fake = browser(lambda page: {"kind": "submit", "data": {"selected": ["b"], "free_text": "x"}})
    result = run_ask(AskRequest(resume=cancelled["resume"]))
    assert result["selected"] == ["b"]
    checked = _checked_inputs(fake.page)
    assert len(checked) == 1
    assert 'data-option-id="b"' in checked[0]
    assert "a &lt;note&gt;</textarea>" in fake.page
    assert "i.defaultChecked" in fake.page


def test_a_draft_is_resumed_once(browser):
    browser(lambda page: _cancel_after({"selected": ["a"], "free_text": ""}))
    resume = run_ask(AskRequest(question="q", options=_a_b()))["resume"]
    browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(resume=resume))
    with pytest.raises(InvalidAsk, match="no draft with resume id"):
        run_ask(AskRequest(resume=resume))


def test_a_multi_question_draft_prefills_each_step_and_keeps_the_header(browser):
    browser(
        lambda page: _cancel_after(
            {"answers": [{"selected": ["a"], "free_text": ""}, {"selected": [], "free_text": "hm"}]}
        )
    )
    questions = [
        Question(id="one", question="one?", options=_a_b()),
        Question(question="two?", options=_a_b()),
    ]
    resume = run_ask(AskRequest(questions=questions, header="Settled: X"))["resume"]

    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(resume=resume))
    assert "Settled: X" in fake.page
    assert len(_checked_inputs(fake.page)) == 1
    assert "hm</textarea>" in fake.page


def test_an_ask_with_nothing_entered_has_no_resume_id(browser):
    browser(lambda page: _cancel_after({"selected": [], "free_text": ""}))
    assert run_ask(AskRequest(question="q", options=_a_b())) == {"cancelled": True}


def test_resume_takes_nothing_else():
    with pytest.raises(InvalidAsk, match="takes nothing else"):
        AskRequest(resume="x", question="q").validated()


def test_a_prefilled_option_is_not_changed_from_when_kept(browser):
    fake = browser(lambda page: {"kind": "submit", "data": {"selected": ["a"], "free_text": ""}})
    run_ask(AskRequest(question="q", options=_a_b(selected=True)))
    assert len(_checked_inputs(fake.page)) == 1


def test_long_first_paragraph_gets_a_hint():
    from text_wall_breaker.ask import LONG_QUESTION_CHARS, _long_question_hint

    long = Question(question="x" * (LONG_QUESTION_CHARS + 1), options=[Option(id="a", label="A")])
    short = Question(question="Short?\n\n" + "y" * 500, options=[Option(id="a", label="A")])
    assert _long_question_hint([short]) is None
    single = _long_question_hint([long])
    second = _long_question_hint([short, long])
    assert single is not None and "The question has" in single
    assert second is not None and "Question 2 has" in second


def test_an_interrupted_single_question_is_marked_and_has_no_resume(browser):
    browser(
        lambda page: {
            "kind": "interrupt",
            "data": {"selected": ["a"], "free_text": " wrong plan ", "interrupted": True},
        }
    )
    result = run_ask(AskRequest(question="q", options=_a_b()))
    assert result == {
        "selected": ["a"],
        "free_text": "wrong plan",
        "expanded": [],
        "changed_from": [],
        "interrupted": True,
    }


def _interrupt(stop: object, *answers: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "interrupt", "data": {"answers": list(answers), "interrupted": stop}}


def test_an_interrupted_ask_ends_at_the_last_answer_given_and_marks_the_stop(browser):
    # Stopped at the second of four; the fourth was answered before coming back, so the
    # empty third stays as a placeholder and nothing follows the fourth.
    browser(
        lambda page: _interrupt(
            1,
            {"selected": ["a"]},
            {"selected": [], "free_text": "not a cache"},
            {},
            {"selected": ["b"]},
        )
    )
    questions = [Question(id=f"q{n}", question=f"{n}?", options=_a_b()) for n in range(5)]
    result = run_ask(AskRequest(questions=questions))
    assert [answer["id"] for answer in result["answers"]] == ["q0", "q1", "q2", "q3"]
    assert [answer.get("interrupted") for answer in result["answers"]] == [
        None,
        True,
        None,
        None,
    ]
    assert result["answers"][1]["free_text"] == "not a cache"
    assert set(result) == {"answers", "resume"}


def test_an_interrupted_ask_reaches_at_least_the_stopped_at_question(browser):
    browser(lambda page: _interrupt(2, {}, {}, {"free_text": "why"}))
    result = run_ask(AskRequest(questions=[_q("one"), _q("two"), _q("three"), _q("four")]))
    assert len(result["answers"]) == 3
    assert result["answers"][2]["interrupted"] is True


def test_resuming_an_interrupted_ask_reopens_every_question_prefilled(browser):
    browser(lambda page: _interrupt(0, {"selected": [], "free_text": "stop note"}))
    resume = run_ask(AskRequest(questions=[_q("one"), _q("two")]))["resume"]

    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(resume=resume))
    assert 'data-step-index="1"' in fake.page
    assert "stop note</textarea>" in fake.page


def test_a_bad_stop_index_marks_the_first_question(browser):
    browser(lambda page: _interrupt("x", {"free_text": "note"}))
    result = run_ask(AskRequest(questions=[_q("one"), _q("two")]))
    assert result["answers"][0]["interrupted"] is True


def test_a_long_question_leaves_no_hint_on_an_undelivered_result(monkeypatch):
    monkeypatch.setattr(ask_module, "open_in_browser", lambda _: None)
    long_question = "x" * 400
    first = run_ask(AskRequest(question=long_question))
    assert first == ask_module.NOT_DELIVERED
    assert "hint" not in ask_module.NOT_DELIVERED


def _abc() -> list[Option]:
    return [Option(id=i, label=i.upper()) for i in "abc"]


def _rank_ask(rank: bool | list[str]) -> AskRequest:
    return AskRequest(questions=[Question(id="r", question="Rank?", options=_abc(), rank=rank)])


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (Question(question="q", options=[Option(id="a", label="A")], rank=True), "at least 2"),
        (Question(question="q", options=_abc(), rank=True, multi_select=True), "no meaning"),
        (Question(question="q", options=_a_b(recommended=True), rank=True), "no meaning"),
        (Question(question="q", options=_abc(), rank=[]), "empty list"),
        (Question(question="q", options=_abc(), rank=["Now", " "]), "empty tier name"),
        (Question(question="q", options=_abc(), rank=["Now", "Now"]), "duplicate tier"),
    ],
)
def test_an_unanswerable_rank_question_is_rejected(question, message):
    with pytest.raises(InvalidAsk, match=message):
        AskRequest(questions=[question]).validated()


def test_a_ranking_answer_lists_every_option_once(browser):
    fake = browser(
        lambda page: {
            "kind": "submit",
            "data": {"answers": [{"order": ["c", "x", "c", "a"], "free_text": " ok "}]},
        }
    )
    answer = run_ask(_rank_ask(True))["answers"][0]
    assert answer["order"] == ["c", "a", "b"]
    assert answer["excluded"] == []
    assert answer["free_text"] == "ok"
    assert "selected" not in answer and "changed_from" not in answer
    assert 'class="pp-rank"' in fake.page
    # The cut line starts last, so nothing is left out until the user moves it.
    assert re.search(r'data-option-id="c".*?</li>\s*<li class="pp-rank-cut"', fake.page, re.S)


def test_options_below_the_cut_line_come_back_excluded(browser):
    browser(
        lambda page: {
            "kind": "submit",
            "data": {"answers": [{"order": ["b"], "excluded": ["c", "b", "x"]}]},
        }
    )
    answer = run_ask(_rank_ask(True))["answers"][0]
    assert answer["order"] == ["b", "a"]
    assert answer["excluded"] == ["c"]


def test_leaving_an_option_out_counts_as_entered_and_resumes_below_the_cut(browser):
    browser(lambda page: _cancel_after({"answers": [{"order": ["a", "b"], "excluded": ["c"]}]}))
    resume = run_ask(_rank_ask(True))["resume"]
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(resume=resume))
    below = fake.page.split('class="pp-rank-cut"', 1)[1].split("</ol>", 1)[0]
    assert 'data-option-id="c"' in below
    assert 'data-option-id="a"' not in below


def test_a_tiers_answer_puts_each_option_in_one_tier_or_unsorted(browser):
    fake = browser(
        lambda page: {
            "kind": "submit",
            "data": {
                "answers": [{"tiers": {"Now": ["b", "x"], "Later": ["b", "a"], "Bogus": ["c"]}}]
            },
        }
    )
    answer = run_ask(_rank_ask(["Now", "Later"]))["answers"][0]
    assert answer["tiers"] == {"Now": ["b"], "Later": ["a"]}
    assert answer["unsorted"] == ["c"]
    assert 'class="pp-tiers"' in fake.page
    assert 'data-tier="Later"' in fake.page


def test_an_untouched_ranking_is_not_worth_resuming(browser):
    browser(lambda page: _cancel_after({"answers": [{"order": ["a", "b", "c"], "free_text": ""}]}))
    assert run_ask(_rank_ask(True)) == {"cancelled": True}


def test_a_resumed_ranking_starts_in_the_users_order(browser):
    browser(lambda page: _cancel_after({"answers": [{"order": ["c", "b", "a"]}]}))
    resume = run_ask(_rank_ask(True))["resume"]
    fake = browser(lambda page: _cancel_after({"answers": [{"order": ["c", "b", "a"]}]}))
    result = run_ask(AskRequest(resume=resume))
    items = re.findall(r'class="pp-rank-item"[^>]*data-option-id="(\w)"', fake.page)
    assert items == ["c", "b", "a"]
    # The resumed order counts as entered (it differs from the agent's), so a second
    # cancel without further moves still keeps a draft.
    assert "resume" in result


def test_a_resumed_tiers_question_starts_with_its_items_sorted(browser):
    browser(lambda page: _cancel_after({"answers": [{"tiers": {"Now": ["b"], "Later": []}}]}))
    resume = run_ask(_rank_ask(["Now", "Later"]))["resume"]
    fake = browser(lambda page: {"kind": "cancel", "data": {}})
    run_ask(AskRequest(resume=resume))
    now = fake.page.split('data-tier="Now"', 1)[1].split('data-tier="Later"', 1)[0]
    assert 'data-option-id="b"' in now
    assert 'data-option-id="a"' not in now


def test_an_interrupted_ask_counts_a_reorder_as_an_answer(browser):
    questions = [
        Question(question="one?", options=_a_b()),
        Question(question="rank?", options=_abc(), rank=True),
    ]
    browser(
        lambda page: {
            "kind": "interrupt",
            "data": {
                "interrupted": 0,
                "answers": [{"free_text": "stop"}, {"order": ["b", "a", "c"]}],
            },
        }
    )
    answers = run_ask(AskRequest(questions=questions))["answers"]
    assert len(answers) == 2
    assert answers[1]["order"] == ["b", "a", "c"]


def test_rank_markup_escapes_labels_and_tier_names():
    from text_wall_breaker.page import build_multi_page

    page = build_multi_page(
        questions=[
            {
                "question": "q",
                "options": [{"id": "a", "label": "<A>"}, {"id": "b", "label": "B"}],
                "rank": ["<now>", "Later"],
            }
        ],
        submit_url="http://127.0.0.1:1/s",
    )
    assert "<A>" not in page and "&lt;A&gt;" in page
    assert 'data-tier="&lt;now&gt;"' in page
    assert ">Unsorted<" in page

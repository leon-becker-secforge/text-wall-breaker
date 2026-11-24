from __future__ import annotations

import json
import threading
import time

import pytest
from support import fetch, wait_until

from text_wall_breaker import ask as ask_module
from text_wall_breaker import listener as listener_module
from text_wall_breaker import session as session_module
from text_wall_breaker.ask import AskRequest, InvalidAsk, Option, Question, run_ask
from text_wall_breaker.listener import Submission
from text_wall_breaker.live_ask import LiveAsk
from text_wall_breaker.live_common import LiveCall, LiveError, Reply
from text_wall_breaker.page import build_multi_page, build_page


class FakeSession:
    def __init__(self):
        self.live = None
        self.closed = False
        self.ended = False
        self.idle = 0.0

    def idle_seconds(self):
        return self.idle

    def publish(self, build_html):  # noqa: ARG002
        return 1

    def end_question(self):
        self.ended = True

    def close(self):
        self.closed = True


def make(questions=None, build_result=None):
    session = FakeSession()
    live = LiveAsk(
        session,
        generation=1,
        questions=questions or [(None, {"a", "b"})],
        build_result=build_result or (lambda submission: {"selected": ["a"]}),
    )
    return session, live


def chat(live, text, ref=None):
    return live.handle({"type": "chat", "text": text, "ref": ref})


def call_as(live, session_id, *, replies=None, wait=True, update=None):
    question, options = update or (None, None)
    return live.call(
        LiveCall(
            session_id=session_id,
            replies=replies or [],
            wait=wait,
            question=question,
            options=options,
        )
    )


def call(live, *, replies=None, wait=True):
    return call_as(live, live.id, replies=replies, wait=wait)


def test_a_message_about_an_option_reaches_the_agent_with_the_option():
    _, live = make()
    assert chat(live, " why A? ", {"question": 0, "option": "a"}) == (200, {})
    assert call(live) == {"messages": [{"chat": "why A?", "option": "a"}]}


def test_a_message_naming_an_unknown_option_is_kept_without_it():
    _, live = make()
    chat(live, "hm", {"question": 0, "option": "zzz"})
    chat(live, "hm", {"question": 7, "option": "a"})
    chat(live, "hm", "not a dict")
    assert call(live)["messages"] == [{"chat": "hm"}] * 3


def test_with_several_questions_the_agent_gets_the_id_or_the_position():
    _, live = make([("first", {"a"}), (None, {"b"})])
    chat(live, "x", {"question": 0, "option": "a"})
    chat(live, "y", {"question": 1, "option": "b"})
    assert call(live)["messages"] == [
        {"chat": "x", "option": "a", "question": "first"},
        {"chat": "y", "option": "b", "question": 1},
    ]


def test_an_empty_message_is_rejected():
    _, live = make()
    assert chat(live, "  ")[0] == 400
    assert live.handle({"type": "thread"})[0] == 400


def test_replies_appear_in_the_page_poll_and_only_the_chat_is_addressable():
    _, live = make()
    assert call(live, replies=[Reply(to="chat", text="**hi**")], wait=False) == {
        "posted": True,
        "messages": [],
    }
    agent = live.poll(restore=False)["chat"][0]
    assert agent["from"] == "agent" and "<strong>hi</strong>" in agent["html"]
    with pytest.raises(LiveError, match="one chat"):
        call(live, replies=[Reply(to="t1", text="x")], wait=False)


def test_the_session_ends_with_the_ask_result_and_the_transcript():
    session, live = make(build_result=lambda submission: {"kind": submission.kind})
    chat(live, "q?")
    live.on_submission(Submission(kind="submit", data={}))
    result = call(live)
    assert result == {"kind": "submit", "chat": [{"from": "operator", "text": "q?"}]}
    assert session.closed and session.ended and session.live is None
    assert session_module.live_for(live.id) is None
    with pytest.raises(LiveError, match="ended or never existed"):
        call(live)


def test_a_silent_tab_ends_the_session_as_abandoned():
    session, live = make(build_result=lambda submission: {"abandoned": submission is None})
    session.idle = 10_000
    assert call(live) == {"abandoned": True}


def test_an_invalid_reply_posts_none_of_the_replies():
    _, live = make()
    replies = [Reply(to="chat", text="one"), Reply(to="t1", text="two")]
    with pytest.raises(LiveError, match="one chat"):
        call(live, replies=replies, wait=False)
    assert live.poll(restore=True)["chat"] == []


def test_the_listener_is_closed_outside_the_session_lock():
    session, live = make()
    held: list[bool] = []

    def close():
        free: list[bool] = []

        def probe():
            took = live._cond.acquire(timeout=1)
            free.append(took)
            if took:
                live._cond.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        held.append(free != [True])

    session.close = close
    live.on_submission(Submission(kind="submit", data={}))
    call(live)
    assert held == [False]


def test_a_second_waiting_call_is_refused():
    _, live = make()
    waiter = threading.Thread(target=call, args=(live,))
    waiter.start()
    wait_until(lambda: live.poll(restore=False)["agent"]["waiting"])
    with pytest.raises(LiveError, match="already waiting"):
        call(live)
    chat(live, "wake")
    waiter.join(timeout=5)


def test_the_page_sees_whether_the_agent_is_waiting():
    _, live = make()
    assert live.poll(restore=False)["agent"]["waiting"] is False
    waiter = threading.Thread(target=call, args=(live,))
    waiter.start()
    wait_until(lambda: live.poll(restore=False)["agent"]["waiting"])
    chat(live, "wake")
    waiter.join(timeout=5)


# ---- the whole path, over HTTP, the way the page and the agent take it ----


def _post(url, payload):
    """POST as a tab showing the current page does: with that page's generation."""
    status = json.loads(fetch(url.replace("/submit?", "/status?")).body)
    reply = fetch(url, {**payload, "generation": status["generation"]})
    assert reply.status < 400, reply


def _operator(session, steps):
    """The tab: loads the first page, then plays `steps` (each a payload for /submit)."""

    def run():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not session.current_page()[0]:
            time.sleep(0.02)
        submit = f"http://127.0.0.1:{session.port}/submit?t={session.token}"
        fetch(session.page_url)
        for step in steps:
            _post(submit, step)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


@pytest.fixture
def tab(monkeypatch):
    monkeypatch.setattr(listener_module, "DELIVERY_SECONDS", 2.0)
    monkeypatch.setattr(ask_module, "open_in_browser", lambda target: None)
    return session_module.get_shared_session()


def test_the_operator_starts_a_discussion_and_the_answer_comes_through_live(tab):
    options = [Option(id="a", label="A"), Option(id="b", label="B")]
    submit = f"http://127.0.0.1:{tab.port}/submit?t={tab.token}"
    operator = _operator(tab, [{"kind": "live_start", "data": {}}])
    started = run_ask(AskRequest(question="Which?", options=options))
    operator.join(timeout=5)

    session_id = started["live"]["session"]
    assert session_id.startswith("la-") and "live(session)" in started["instruction"]
    # The tab left the shared slot: a later call binds its own listener and tab.
    assert session_module.get_shared_session() is not tab

    live = session_module.live_for(session_id)
    _post(
        submit,
        {
            "kind": "live",
            "data": {"type": "chat", "text": "why A?", "ref": {"question": 0, "option": "a"}},
        },
    )
    assert call_as(live, session_id, wait=True)["messages"] == [{"chat": "why A?", "option": "a"}]
    call_as(live, session_id, replies=[Reply(to="chat", text="cheaper")], wait=False)
    _post(submit, {"kind": "submit", "data": {"selected": ["a"], "free_text": ""}})

    result = call_as(live, session_id, wait=True)
    assert result["selected"] == ["a"]
    assert [m["from"] for m in result["chat"]] == ["operator", "agent"]
    assert session_module.live_for(session_id) is None


def _start(tab, request, steps):
    """A live ask started by the operator, after `steps` (payloads) were posted first."""
    operator = _operator(tab, [*steps, {"kind": "live_start", "data": {}}])
    started = run_ask(request)
    operator.join(timeout=5)
    return started["live"]["session"], session_module.live_for(started["live"]["session"])


def test_the_agent_replaces_the_options_and_learns_which_picks_are_gone(tab):
    submit = f"http://127.0.0.1:{tab.port}/submit?t={tab.token}"
    picked = {"kind": "progress", "data": {"selected": ["a"], "free_text": ""}}
    request = AskRequest(
        question="Which?", options=[Option(id="a", label="A"), Option(id="b", label="B")]
    )
    session_id, live = _start(tab, request, [picked])

    result = call_as(
        live,
        session_id,
        wait=False,
        update=("Which one now?", [{"id": "b", "label": "B"}, {"id": "c", "label": "C"}]),
    )
    assert result == {"posted": True, "messages": [], "refreshed": {"dropped_picks": ["a"]}}
    page, generation = tab.current_page()
    html = page.decode()
    assert generation == 2 and 'liveMode: "on"' in html and "Which one now?" in html
    assert 'data-option-id="c"' in html and 'data-option-id="a"' not in html
    assert live.poll(restore=False)["generation"] == 2

    # The new option is addressable; the removed one no longer is.
    _post(submit, {"kind": "live", "data": {"type": "chat", "text": "c?", "ref": {"option": "c"}}})
    _post(submit, {"kind": "live", "data": {"type": "chat", "text": "a?", "ref": {"option": "a"}}})
    assert call_as(live, session_id, wait=True)["messages"] == [
        {"chat": "c?", "option": "c"},
        {"chat": "a?"},
    ]
    # The answer is read against the new options.
    _post(submit, {"kind": "submit", "data": {"selected": ["a", "c"], "free_text": ""}})
    assert call_as(live, session_id, wait=True)["selected"] == ["c"]


def test_a_call_without_question_or_options_publishes_nothing(tab):
    request = AskRequest(question="Which?", options=[Option(id="a", label="A")])
    session_id, live = _start(tab, request, [])
    result = call_as(live, session_id, wait=False)
    assert result == {"posted": True, "messages": []}
    assert tab.current_page()[1] == 1
    live._session.close()


def test_a_refresh_that_would_leave_no_way_to_answer_is_refused(tab):
    request = AskRequest(
        question="Which?", options=[Option(id="a", label="A")], allow_free_text=False
    )
    session_id, live = _start(tab, request, [])
    with pytest.raises(LiveError, match="no way to answer"):
        call_as(live, session_id, wait=False, update=(None, []))
    live._session.close()


def test_a_multi_question_ask_cannot_be_refreshed(tab):
    questions = [Question(question="One", options=[Option(id="a", label="A")])]
    session_id, live = _start(tab, AskRequest(questions=questions), [])
    with pytest.raises(LiveError, match="one question"):
        call_as(live, session_id, wait=False, update=("x", None))
    live._session.close()


def test_a_refresh_after_the_end_is_dropped(tab):
    submit = f"http://127.0.0.1:{tab.port}/submit?t={tab.token}"
    request = AskRequest(question="Which?", options=[Option(id="a", label="A")])
    session_id, live = _start(tab, request, [])
    _post(submit, {"kind": "submit", "data": {"selected": ["a"], "free_text": ""}})
    result = call_as(live, session_id, wait=False, update=("new", None))
    assert result["selected"] == ["a"] and result["refresh_dropped"] is True


def test_an_agent_can_open_the_ask_live_straight_away(tab):
    operator = _operator(tab, [])
    started = run_ask(AskRequest(question="Which?", options=[Option(id="a", label="A")], live=True))
    operator.join(timeout=5)
    assert started["live"]["session"].startswith("la-")
    live = session_module.live_for(started["live"]["session"])
    assert isinstance(live, LiveAsk)
    live._session.close()


def test_live_needs_the_shared_tab(tab):
    assert tab.try_claim()
    with pytest.raises(InvalidAsk, match="live=true needs the shared browser tab"):
        run_ask(AskRequest(question="q", options=[Option(id="a", label="A")], live=True))


def test_a_multi_question_ask_reports_the_question_of_a_message(tab):
    questions = [
        Question(id="first", question="One", options=[Option(id="a", label="A")]),
        Question(question="Two", options=[Option(id="b", label="B")]),
    ]
    submit = f"http://127.0.0.1:{tab.port}/submit?t={tab.token}"
    operator = _operator(tab, [{"kind": "live_start", "data": {}}])
    started = run_ask(AskRequest(questions=questions))
    operator.join(timeout=5)
    session_id = started["live"]["session"]
    _post(
        submit,
        {
            "kind": "live",
            "data": {"type": "chat", "text": "?", "ref": {"question": 1, "option": "b"}},
        },
    )
    live = session_module.live_for(session_id)
    assert isinstance(live, LiveAsk)
    assert call_as(live, session_id, wait=True)["messages"] == [
        {"chat": "?", "option": "b", "question": 1}
    ]
    live._session.close()


# ---- the page ----


def _page(**kwargs):
    return build_page(
        question="q",
        options=[{"id": "a", "label": "A"}],
        multi_select=False,
        allow_free_text=True,
        submit_url="http://127.0.0.1:1/submit?t=x",
        status_url="http://127.0.0.1:1/status?t=x",
        **kwargs,
    )


def test_a_page_without_live_carries_no_discussion_script():
    for html in (_page(), _page(live="off")):
        assert "window.twbAsk" in html  # the hooks are always there, cheap and inert
        assert "twb-live-panel" not in html and "game-icons.net" not in html


@pytest.mark.parametrize("mode", ["offer", "on"])
def test_a_live_page_carries_the_panel_the_bubble_and_the_attribution(mode):
    html = _page(live=mode)
    assert f"liveMode: {json.dumps(mode)}" in html
    assert "twb-live-panel" in html and "twb-live-ask" in html
    assert "SeregaCthtuf" in html and "CC BY 3.0" in html
    assert "__ICON__" not in html and "__LIVE" not in html


def test_the_multi_question_page_is_live_too():
    html = build_multi_page(
        questions=[{"question": "q", "options": [{"id": "a", "label": "A"}]}],
        submit_url="http://127.0.0.1:1/submit?t=x",
        status_url="http://127.0.0.1:1/status?t=x",
        live="offer",
    )
    assert "twb-live-panel" in html and 'liveMode: "offer"' in html and "__LIVE" not in html


@pytest.mark.parametrize(
    "extra", [{"refresh": True}, {"git_range": "HEAD"}, {"paths": []}, {"pages": []}, {"notes": []}]
)
def test_an_ask_session_rejects_review_arguments(extra):
    _, live = make()
    with pytest.raises(LiveError, match="review sessions"):
        live.call(LiveCall(session_id=live.id, wait=False, **extra))

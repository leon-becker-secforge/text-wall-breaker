from __future__ import annotations

import threading
from typing import Any

import pytest
from support import make_live_review as make
from support import wait_until

from text_wall_breaker.listener import Submission
from text_wall_breaker.live import LiveError, Reply
from text_wall_breaker.live_common import LiveCall
from text_wall_breaker.review import Page

DIFF_SHIFTED = """--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,4 @@
+zero
 one
-two
+TWO
 three
"""

DIFF_GONE = """--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+2
 three
"""


def call(live, session_id: str | None = None, **kwargs: Any):
    return live.call(LiveCall(session_id=session_id or live.id, **kwargs))


def comment(line=2, text="why?", **extra):
    return {"path": "a.txt", "side": "new", "line": line, "text": text, **extra}


def test_a_thread_reaches_the_agent_with_its_context():
    _, live = make()
    status, body = live.handle({"type": "thread", "comment": comment()})
    assert status == 200 and body == {"id": "t1"}
    result = call(live)
    assert result == {
        "messages": [
            {
                "thread": "t1",
                "path": "a.txt",
                "side": "new",
                "line": 2,
                "text": "why?",
                "context": "TWO",
            }
        ]
    }


def test_a_blocking_call_returns_when_the_operator_writes():
    _, live = make()
    out = {}
    worker = threading.Thread(target=lambda: out.update(call(live)))
    worker.start()
    wait_until(lambda: live.poll(restore=False)["agent"]["waiting"])
    live.handle({"type": "chat", "text": "rename it"})
    worker.join(2)
    assert out == {"messages": [{"chat": "rename it"}]}
    assert live.poll(restore=False)["agent"]["waiting"] is False


def test_wait_false_posts_and_returns_what_is_pending():
    _, live = make()
    live.handle({"type": "chat", "text": "first"})
    result = call(live, replies=[Reply(to="chat", text="On it.")], wait=False)
    assert result == {"posted": True, "messages": [{"chat": "first"}]}
    chat = live.poll(restore=False)["chat"]
    assert [m["from"] for m in chat] == ["operator", "agent"]


def test_a_reply_to_an_unknown_thread_is_refused():
    _, live = make()
    with pytest.raises(LiveError, match="no thread"):
        call(live, replies=[Reply(to="t9", text="hi")], wait=False)


def test_done_marks_the_thread_and_an_operator_reply_clears_it():
    _, live = make()
    live.handle({"type": "thread", "comment": comment()})
    call(live, replies=[Reply(to="t1", text="fixed", done=True)], wait=False)
    assert live.poll(restore=False)["threads"][0]["done"] is True
    live.handle({"type": "reply", "thread": "t1", "text": "not quite"})
    assert live.poll(restore=False)["threads"][0]["done"] is False


def test_a_report_sent_while_the_agent_works_is_queued_and_a_late_refresh_dropped():
    session, live = make()
    live.on_submission(Submission(kind="submit", data={"comments": [comment()], "generation": 1}))
    result = call(live, pages=[Page(path="a.txt", diff=DIFF_SHIFTED)])
    assert result["refresh_dropped"] is True
    assert result["comments"][0]["line"] == 2
    assert session.published == []
    assert session.closed and session.ended and session.live is None
    with pytest.raises(LiveError):
        call(live)


def test_a_refresh_moves_a_comment_with_its_line():
    session, live = make()
    live.on_progress({"comments": [comment()], "generation": 1})
    call(live, pages=[Page(path="a.txt", diff=DIFF_SHIFTED)], wait=False)
    assert session.published == ["page 2"]
    restored = live.poll(restore=True)["restore"]
    assert [c["line"] for c in restored["comments"]] == [3]
    assert restored["outdated"] == []


def test_a_refresh_that_removes_the_line_makes_the_comment_outdated():
    _, live = make()
    live.handle({"type": "thread", "comment": comment()})
    live.on_progress({"comments": [comment(thread="t1")], "generation": 1})
    call(live, pages=[Page(path="a.txt", diff=DIFF_GONE)], wait=False)
    poll = live.poll(restore=True)
    assert poll["restore"]["comments"] == []
    [old] = poll["restore"]["outdated"]
    assert old["context"] == "TWO" and old["thread"] == "t1"
    assert poll["threads"][0]["outdated"] is True
    live.on_submission(Submission(kind="submit", data={"comments": [], "generation": 2}))
    result = call(live)
    assert result["comments"] == [
        {
            "path": "a.txt",
            "side": "new",
            "line": 2,
            "text": "why?",
            "context": "TWO",
            "outdated": True,
        }
    ]
    assert result["threads"][0]["outdated"] is True


def test_a_late_progress_report_from_the_old_page_is_reanchored():
    _, live = make()
    call(live, pages=[Page(path="a.txt", diff=DIFF_SHIFTED)], wait=False)
    live.on_progress({"comments": [comment()], "generation": 1})
    assert [c["line"] for c in live.poll(restore=True)["restore"]["comments"]] == [3]


def test_the_interdiff_runs_from_what_the_operator_last_saw():
    _, live = make()
    live.handle({"type": "seen", "path": "a.txt"})
    call(live, pages=[Page(path="a.txt", diff=DIFF_SHIFTED)], wait=False)
    rows = live.poll(restore=True)["restore"]["interdiffs"]["a.txt"]
    assert {"kind": "add", "text": "zero", "old": None, "new": 1} in rows


def test_a_closed_tab_ends_the_session_as_abandoned(monkeypatch):
    session, live = make()
    session.idle = 10_000
    result = call(live)
    assert result["abandoned"] is True
    assert session.closed


def test_a_session_whose_tab_is_gone_frees_its_listener_without_a_call():
    session, live = make()
    assert not live._reap()
    assert not session.closed
    session.idle = 10_000
    assert live._reap()
    assert session.closed
    # The agent's next call still learns how the session ended.
    assert call(live)["abandoned"] is True


def test_a_session_with_a_call_in_progress_is_not_reaped():
    session, live = make()
    session.idle = 10_000
    live._busy = True
    assert not live._reap()
    assert not session.closed


def _lock_is_free(lock) -> bool:
    """Whether another thread could take `lock` right now."""
    free: list[bool] = []

    def probe():
        took = lock.acquire(timeout=1)
        free.append(took)
        if took:
            lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return free == [True]


def test_the_listener_is_closed_outside_the_session_lock():
    # Closing waits for the listener's serve loop; a request handler holding out for the
    # lock would deadlock it.
    session, live = make()
    held: list[bool] = []
    session.close = lambda: held.append(not _lock_is_free(live._cond))
    live.on_submission(Submission(kind="submit", data={"comments": [], "generation": 1}))
    call(live)
    assert held == [False]


def test_an_invalid_reply_posts_none_of_the_replies():
    _, live = make()
    replies = [Reply(to="chat", text="one"), Reply(to="t99", text="two")]
    for _ in range(2):
        with pytest.raises(LiveError, match="t99"):
            call(live, replies=replies, wait=False)
    assert live.poll(restore=True)["chat"] == []


def test_a_git_range_without_refresh_is_not_ignored():
    _, live = make()
    with pytest.raises(LiveError, match="git_range and paths need a session opened with repo"):
        call(live, git_range="HEAD~1", wait=False)


def test_a_second_call_during_a_slow_refresh_is_refused(monkeypatch):
    _, live = make()
    entered, release = threading.Event(), threading.Event()
    carry = live._carry_notes

    def slow_carry(*args):
        entered.set()
        release.wait(5)
        return carry(*args)

    monkeypatch.setattr(live, "_carry_notes", slow_carry)
    first = threading.Thread(target=lambda: call(live, refresh=True, wait=False))
    first.start()
    assert entered.wait(5)
    try:
        with pytest.raises(LiveError, match="already"):
            call(live, wait=False)
    finally:
        release.set()
        first.join(5)


def test_a_stale_session_id_is_refused():
    _, live = make()
    with pytest.raises(LiveError, match="no live session"):
        call(live, session_id="ls-0")


def test_a_review_session_rejects_ask_arguments():
    _, live = make()
    with pytest.raises(LiveError, match="ask sessions"):
        call(live, question="new?")
    with pytest.raises(LiveError, match="ask sessions"):
        call(live, options=[])

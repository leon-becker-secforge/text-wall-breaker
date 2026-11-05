import threading
import time

import pytest

from pretty_prompt.listener import LiveHandler, Submission
from pretty_prompt.live import LiveError, LiveSession, Reply
from pretty_prompt.review import Page, ReviewRequest

DIFF = """--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""

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


class FakeSession:
    def __init__(self):
        self.live: LiveHandler | None = None
        self.generation = 1
        self.published = []
        self.released = False
        self.ended = False
        self.idle = 0.0

    def publish(self, build_html):
        self.generation += 1
        self.published.append(build_html(self.generation))
        return self.generation

    def idle_seconds(self):
        return self.idle

    def end_question(self):
        self.ended = True

    def release(self):
        self.released = True


def make(diff=DIFF):
    session = FakeSession()
    request = ReviewRequest(pages=[Page(path="a.txt", diff=diff)]).validated()
    live = LiveSession(
        session,
        request,
        None,
        build_html=lambda req, g: f"page {g}",
        load_repo=lambda source: ([], {}),
        generation=1,
    )
    return session, live


def call(live, **kwargs):
    args = {
        "session_id": live.id,
        "replies": [],
        "refresh": False,
        "git_range": None,
        "paths": None,
        "pages": None,
        "notes": None,
        "wait": True,
    }
    args.update(kwargs)
    return live.call(**args)


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
    time.sleep(0.2)
    assert live.poll(restore=False)["agent"]["waiting"] is True
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
    assert session.released and session.ended and session.live is None
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
    assert session.released


def test_a_stale_session_id_is_refused():
    _, live = make()
    with pytest.raises(LiveError, match="no live session"):
        call(live, session_id="ls-0")

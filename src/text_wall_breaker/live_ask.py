"""A live `ask` session: one question page the operator and the agent talk across.

See ADR 0005 and CONTEXT.md's "Live session". The page talks to this through its own
`SessionListener` (`POST /submit` with kind "live", `GET /live`), the agent through `live`
calls, each running `LiveAsk.call` in a worker thread. The chat is the only exchange: one
per question, each message optionally about one option. The operator ends the session by
sending the answer, cancelling or closing the tab, and the call that sees the end returns
the result a plain `ask` returns.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from .listener import Submission
from .live_common import (
    LiveBase,
    LiveCall,
    LiveError,
    Reply,
    Tab,
    no_such_session,
)
from .markdown import render_markdown
from .session import detach_shared_session, forget_live, register_live

__all__ = ["ASK_ATTACH_INSTRUCTION", "LiveAsk"]

ASK_ATTACH_INSTRUCTION = (
    "Live ask: call live(session) now and keep calling it until it returns the answer. "
    "Each call returns the operator's new messages; answer with replies to chat (with "
    "several questions, to the question's id or position). Use wait=false to acknowledge "
    "before long work."
)

_MAX_MESSAGE_CHARS = 20_000
_MAX_LABEL_CHARS = 200

# What `LiveAsk` needs to republish the page after the agent replaced the question or its
# options: given the new question and options (None: unchanged), it returns the page
# builder, the new `questions` and the option ids the operator had picked that are gone.
type Refresher = Callable[
    [str | None, list[Any] | None],
    tuple[Callable[[int], str], list[tuple[str | None, set[str]]], list[str]],
]


class LiveAsk(LiveBase):
    """State of one live `ask`, shared by the page's requests and the agent's calls."""

    def __init__(
        self,
        session: Tab,
        *,
        generation: int,
        questions: list[tuple[str | None, set[str]]],
        build_result: Callable[[Submission | None], dict[str, Any]],
        prepare_refresh: Refresher | None = None,
    ) -> None:
        """`questions` lists each question's id (if it has one) and option ids, in order;
        `build_result` makes the `ask` result from the operator's final submission (None:
        the tab was abandoned). `prepare_refresh` validates a replacement of the question
        or its options and returns what to publish; None if the ask cannot be refreshed."""
        self.id = "la-" + secrets.token_hex(4)
        self._session = session
        self._questions = questions
        self._build_result = build_result
        self._prepare_refresh = prepare_refresh
        self._cond = threading.Condition()
        self._generation = generation
        self._chat: list[dict[str, Any]] = []
        self._inbox: list[dict[str, Any]] = []
        self._version = 0
        # `_blocking`: a call waits for the operator (shown on the page). `_busy`: a call
        # is anywhere past its first check, refresh included.
        self._blocking = False
        self._busy = False
        self._since = time.monotonic()
        self._final: Submission | None = None
        self._abandoned = False
        self._done = False
        self._closed = False
        session.live = self
        detach_shared_session(session)
        register_live(self.id, self)
        self._start_reaper()

    # ---- the page ----

    def poll(self, *, restore: bool) -> dict[str, Any]:  # noqa: ARG002
        with self._cond:
            return {
                "session": self.id,
                "generation": self._generation,
                "version": self._version,
                "agent": {
                    "waiting": self._blocking,
                    "seconds": int(time.monotonic() - self._since),
                },
                "chat": self._chat,
                "ended": self._final is not None or self._abandoned,
            }

    def handle(self, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """One operator event from the page: a chat message."""
        with self._cond:
            if self._final is not None or self._done:
                return 409, {"error": "the session has ended"}
            if data.get("type") != "chat":
                return 400, {"error": "unknown event"}
            text = data.get("text")
            if not isinstance(text, str) or not text.strip():
                return 400, {"error": "empty message"}
            text = text.strip()[:_MAX_MESSAGE_CHARS]
            ref = self._reference(data.get("ref"))
            if ref is None:
                return 400, {"error": "unknown question"}
            index, option, label = ref
            entry: dict[str, Any] = {"from": "operator", "text": text, "q": index}
            inbox: dict[str, Any] = {"chat": text}
            if option is not None:
                entry["option"] = inbox["option"] = option
                if label:
                    entry["label"] = label
            if len(self._questions) > 1:
                inbox["question"] = self._questions[index][0] or index
            self._chat.append(entry)
            self._inbox.append(inbox)
            self._version += 1
            self._cond.notify_all()
            return 200, {}

    def _reference(self, raw: object) -> tuple[int, str | None, str] | None:
        """The question a message belongs to, the option it is about (if the page named a
        known one) and that option's label as the page showed it; None for an unknown
        question. The page names the question by its position. A single-question ask
        needs no reference: its one question is the default. The agent gets the question's
        id, or its position (from 0) when it has none, only with several questions."""
        single = len(self._questions) == 1
        if not isinstance(raw, dict):
            return (0, None, "") if single else None
        index = raw.get("question", 0 if single else None)
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if not 0 <= index < len(self._questions):
            return None
        option = raw.get("option")
        if not isinstance(option, str) or option not in self._questions[index][1]:
            return index, None, ""
        label = raw.get("label")
        return index, option, label.strip()[:_MAX_LABEL_CHARS] if isinstance(label, str) else ""

    def on_progress(self, data: dict[str, Any]) -> None:
        """The listener keeps the answers so far for `resume`; nothing more to do here."""

    def on_submission(self, submission: Submission) -> None:
        if submission.kind not in ("submit", "cancel", "interrupt"):
            return
        with self._cond:
            self._final = submission
            self._version += 1
            self._cond.notify_all()

    # ---- the agent ----

    def call(self, request: LiveCall) -> dict[str, Any]:
        """One `live` call: post, maybe replace the question or options, then wait for the
        operator's next message."""
        try:
            return self._call(request)
        finally:
            self._close_if_done()

    def _call(self, request: LiveCall) -> dict[str, Any]:
        if request.has_review_arguments():
            raise LiveError("refresh, git_range, paths, pages and notes are for review sessions")
        update = (request.question, request.options) if request.has_ask_arguments() else None
        with self._cond:
            if request.session_id != self.id or self._done:
                raise no_such_session(request.session_id)
            # `_busy` from here on, not only while waiting: a refresh runs outside the
            # lock, and a second call slipping in then would refresh from the same state.
            if self._busy:
                raise LiveError("another live call is already waiting on this session")
            if self._final is not None or self._abandoned:
                return self._finish(refresh_dropped=update is not None)
            targets = [self._reply_target(reply) for reply in request.replies]
            for reply, index in zip(request.replies, targets, strict=True):
                self._post(reply, index)
            self._busy = True
            self._since = time.monotonic()
            self._version += 1
        try:
            extra: dict[str, Any] = {}
            if update is not None:
                extra["refreshed"] = {"dropped_picks": self._refresh(*update)}
            return self._wait(request, extra)
        finally:
            with self._cond:
                self._busy = False

    def _refresh(self, question: str | None, options: list[Any] | None) -> list[str]:
        """Republish the page with a new question or options; the option ids picked that
        no longer exist. The page reloads itself and keeps what survives."""
        if self._prepare_refresh is None:
            raise LiveError("this session cannot be refreshed")
        if question is None and options is None:
            return []
        builder, questions, dropped = self._prepare_refresh(question, options)
        generation = self._session.publish(builder)
        with self._cond:
            self._questions = questions
            self._generation = generation
            self._version += 1
        return dropped

    def _reply_target(self, reply: Reply) -> int:
        """The question a reply answers: its id or position, or "chat" with one question."""
        if not reply.text.strip():
            raise LiveError("a reply needs text")
        if len(self._questions) == 1:
            if reply.to != "chat":
                raise LiveError('a live ask with one question has one chat: reply to "chat"')
            return 0
        for index, (question_id, _) in enumerate(self._questions):
            if reply.to == question_id:
                return index
        if reply.to.isdecimal() and int(reply.to) < len(self._questions):
            return int(reply.to)
        raise LiveError(
            "a live ask has one chat per question: reply to a question's id, or its "
            "position from 0 when it has none"
        )

    def _post(self, reply: Reply, index: int) -> None:
        text = reply.text.strip()
        self._chat.append(
            {"from": "agent", "text": text, "html": render_markdown(text), "q": index}
        )

    def _transcript(self) -> list[dict[str, Any]]:
        """Every message, without the page's own fields: flat for one question, grouped
        by question (its id, else position) for several."""
        flat = [
            {k: v for k, v in message.items() if k not in ("html", "q", "label")}
            for message in self._chat
        ]
        if len(self._questions) == 1:
            return flat
        groups: list[dict[str, Any]] = []
        for index, (question_id, _) in enumerate(self._questions):
            messages = [m for m, raw in zip(flat, self._chat, strict=True) if raw["q"] == index]
            if messages:
                groups.append({"question": question_id or index, "messages": messages})
        return groups

    def _finish(self, *, refresh_dropped: bool = False) -> dict[str, Any]:
        result = self._build_result(self._final)
        if refresh_dropped:
            result["refresh_dropped"] = True
        if self._chat:
            result["chat"] = self._transcript()
        self._done = True
        self._session.live = None
        self._session.end_question()
        forget_live(self.id)
        return result

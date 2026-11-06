"""A live `ask` session: one question page the operator and the agent talk across.

See ADR 0005 and CONTEXT.md's "Live session". The page talks to this through its own
`SessionListener` (`POST /submit` with kind "live", `GET /live`), the agent through `live`
calls, each running `LiveAsk.call` in a worker thread. The chat is the only exchange: one
per page, each message optionally about one option. The operator ends the session by
sending the answer, cancelling or closing the tab, and the call that sees the end returns
the result a plain `ask` returns.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

from .listener import ABANDON_SECONDS, Submission
from .live_common import POLL_SECONDS, LiveError, Reply, Tab
from .markdown import render_markdown
from .session import detach_shared_session, forget_live, register_live

__all__ = ["ASK_ATTACH_INSTRUCTION", "LiveAsk"]

ASK_ATTACH_INSTRUCTION = (
    "Live ask: call live(session) now and keep calling it until it returns the answer. "
    "Each call returns the operator's new messages; answer with replies to chat. Use "
    "wait=false to acknowledge before long work."
)

_MAX_MESSAGE_CHARS = 20_000


class LiveAsk:
    """State of one live `ask`, shared by the page's requests and the agent's calls."""

    def __init__(
        self,
        session: Tab,
        *,
        generation: int,
        questions: list[tuple[str | None, set[str]]],
        build_result: Callable[[Submission | None], dict[str, Any]],
    ) -> None:
        """`questions` lists each question's id (if it has one) and option ids, in order;
        `build_result` makes the `ask` result from the operator's final submission (None:
        the tab was abandoned)."""
        self.id = "la-" + secrets.token_hex(4)
        self._session = session
        self._questions = questions
        self._build_result = build_result
        self._cond = threading.Condition()
        self._generation = generation
        self._chat: list[dict[str, Any]] = []
        self._inbox: list[dict[str, Any]] = []
        self._version = 0
        self._blocking = False
        self._since = time.monotonic()
        self._final: Submission | None = None
        self._abandoned = False
        self._done = False
        session.live = self
        detach_shared_session(session)  # type: ignore[arg-type]
        register_live(self.id, self)

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
            self._chat.append({"from": "operator", "text": text, **ref})
            self._inbox.append({"chat": text, **ref})
            self._version += 1
            self._cond.notify_all()
            return 200, {}

    def _reference(self, raw: object) -> dict[str, Any]:
        """The option a message is about, if the page named a known one.

        The page names the question by its position. With several questions the agent
        gets the question's id, or its position (from 0) when it has none.
        """
        if not isinstance(raw, dict):
            return {}
        index = raw.get("question", 0)
        option = raw.get("option")
        if not isinstance(index, int) or isinstance(index, bool):
            return {}
        if not 0 <= index < len(self._questions) or not isinstance(option, str):
            return {}
        question_id, option_ids = self._questions[index]
        if option not in option_ids:
            return {}
        if len(self._questions) == 1:
            return {"option": option}
        return {"option": option, "question": question_id if question_id else index}

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

    def call(self, *, session_id: str, replies: list[Reply], wait: bool) -> dict[str, Any]:
        """One `live` call: post, then wait for the operator's next message."""
        with self._cond:
            if session_id != self.id or self._done:
                raise LiveError(f"no live session {session_id!r}: it ended or never existed")
            if self._blocking:
                raise LiveError("another live call is already waiting on this session")
            if self._final is not None or self._abandoned:
                return self._finish()
            for reply in replies:
                self._post(reply)
            self._since = time.monotonic()
            self._version += 1
            if not wait:
                return {"posted": True, "messages": self._take_inbox()}
            self._blocking = True
            self._version += 1
            try:
                while True:
                    if self._final is not None:
                        return self._finish()
                    if self._inbox:
                        return {"messages": self._take_inbox()}
                    if self._session.idle_seconds() > ABANDON_SECONDS:
                        self._abandoned = True
                        return self._finish()
                    self._cond.wait(POLL_SECONDS)
            finally:
                self._blocking = False
                self._since = time.monotonic()
                self._version += 1

    def _post(self, reply: Reply) -> None:
        text = reply.text.strip()
        if not text:
            raise LiveError("a reply needs text")
        if reply.to != "chat":
            raise LiveError('a live ask has one chat: reply to "chat"')
        self._chat.append({"from": "agent", "text": text, "html": render_markdown(text)})

    def _take_inbox(self) -> list[dict[str, Any]]:
        messages, self._inbox = self._inbox, []
        return messages

    def _finish(self) -> dict[str, Any]:
        result = self._build_result(self._final)
        if self._chat:
            result["chat"] = [
                {k: v for k, v in message.items() if k != "html"} for message in self._chat
            ]
        self._done = True
        self._session.live = None
        self._session.end_question()
        forget_live(self.id)
        self._session.close()
        return result

"""What a live review (`live.py`) and a live ask (`live_ask.py`) share.

Its own module because `ask.py` starts a live ask while `live.py` imports `review.py`,
which imports `ask.py`: the shared parts cannot sit in `live.py`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .listener import LiveHandler

__all__ = ["POLL_SECONDS", "LiveCall", "LiveError", "Reply", "Tab", "no_such_session"]

# How long a `live` wait sleeps between checks for abandonment.
POLL_SECONDS = 0.5


class LiveError(ValueError):
    """A `live` call the session cannot accept."""


def no_such_session(session_id: str) -> LiveError:
    return LiveError(f"no live session {session_id!r}: it ended or never existed")


class Tab(Protocol):
    """What a live session uses of the `SessionListener` it took over from the shared one."""

    live: LiveHandler | None

    def publish(self, build_html: Callable[[int], str]) -> int: ...
    def idle_seconds(self) -> float: ...
    def end_question(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Reply:
    to: str
    text: str
    done: bool = False


@dataclass(frozen=True)
class LiveCall:
    """The arguments of one `live` tool call, whichever kind of session it addresses.

    Each kind of session rejects the arguments that belong to the other, so a call that
    mixes them fails the same way for both. `pages`, `notes` and `options` are untyped
    here because their models live in modules that import this one.
    """

    session_id: str
    replies: list[Reply] = field(default_factory=list)
    wait: bool = True
    refresh: bool = False
    git_range: str | None = None
    paths: list[str] | None = None
    pages: list[Any] | None = None
    notes: list[Any] | None = None
    question: str | None = None
    options: list[Any] | None = None
    # Set when the caller abandoned the call (the user interrupted the agent): the wait
    # must end without taking anything, or the session stays locked against the next call.
    cancel: threading.Event | None = None

    def has_review_arguments(self) -> bool:
        return bool(
            self.refresh
            or self.git_range is not None
            or self.paths is not None
            or self.pages is not None
            or self.notes is not None
        )

    def has_ask_arguments(self) -> bool:
        return self.question is not None or self.options is not None

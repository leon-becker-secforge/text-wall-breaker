"""What a live review (`live.py`) and a live ask (`live_ask.py`) share.

Its own module because `ask.py` starts a live ask while `live.py` imports `review.py`,
which imports `ask.py`: the shared parts cannot sit in `live.py`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .listener import ABANDON_SECONDS, LiveHandler

__all__ = [
    "POLL_SECONDS",
    "LiveCall",
    "LiveError",
    "Reaper",
    "Reply",
    "Tab",
    "no_such_session",
]

# How long a `live` wait sleeps between checks for abandonment.
POLL_SECONDS = 0.5

# How often a session no call waits on checks whether its tab went silent.
REAP_SECONDS = 30.0


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


class Reaper:
    """Closes a live session's listener: after the call that ends it, or once its tab is
    gone with no call waiting.

    A wait notices abandonment itself; the reaper thread covers an agent that never calls
    again, whose session would otherwise hold a thread and a socket until the process
    ends. The session stays registered: the agent's next call still gets the abandoned
    result.
    """

    _cond: threading.Condition
    _session: Tab
    _busy: bool
    _done: bool
    _closed: bool
    _abandoned: bool
    _version: int

    def _start_reaper(self) -> None:
        self._reaper_stop = threading.Event()
        threading.Thread(target=self._reap_loop, daemon=True).start()

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(REAP_SECONDS):
            if self._reap():
                return

    def _close_if_done(self) -> None:
        """After a call: close the listener once the session has ended.

        Outside `_cond`: closing waits for the listener, whose handlers take it.
        """
        with self._cond:
            close = self._done and not self._closed
            self._closed |= close
        if close:
            self._reaper_stop.set()
            self._session.close()

    def _reap(self) -> bool:
        """Close the listener if the tab is gone; True once there is nothing to watch."""
        with self._cond:
            if self._done or self._closed:
                return True
            if self._busy or self._session.idle_seconds() <= ABANDON_SECONDS:
                return False
            self._abandoned = True
            self._closed = True
            self._version += 1
        self._session.close()
        return True


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

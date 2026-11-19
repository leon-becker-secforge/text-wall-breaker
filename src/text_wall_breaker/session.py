"""The one `SessionListener` shared by every `ask` and `show` call in this process.

Factored out of `ask.py` so both tools reuse the same already-open browser tab instead
of each keeping its own — a `show` right after an `ask` (or vice versa) lands in the
same tab rather than opening a second one. Created lazily, not at import time, so
importing this module never binds a port as a side effect (tests import it freely).

A live session takes its listener out of this slot (`detach_shared_session`) and is found
again by id (`live_for`), so several can run at once, each in its own tab.
"""

from __future__ import annotations

import secrets
import threading
from typing import TYPE_CHECKING, Any, Protocol

from .listener import SessionListener

if TYPE_CHECKING:
    # live_common imports listener only, but live and live_ask import this module.
    from .live_common import LiveCall

__all__ = [
    "detach_shared_session",
    "forget_live",
    "get_shared_session",
    "live_for",
    "register_live",
]


class LiveTarget(Protocol):
    """What the `live` tool calls on a running session (`LiveSession` or `LiveAsk`)."""

    id: str

    def call(self, request: LiveCall) -> dict[str, Any]: ...


_lock = threading.Lock()
_session: SessionListener | None = None
_live: dict[str, LiveTarget] = {}


def get_shared_session() -> SessionListener:
    global _session
    with _lock:
        if _session is None:
            _session = SessionListener(secrets.token_hex(16))
        return _session


def detach_shared_session(session: object) -> None:
    """Hand `session` and its tab over to whoever holds it, for good.

    A live session keeps its tab for as long as it runs. After this, the next
    `get_shared_session()` binds a new listener, so later calls open their own tab and
    are no longer blocked by the live one. A no-op if `session` is not the shared one.
    `session` is compared by identity, so a live session passes the `Tab` it was given.
    """
    global _session
    with _lock:
        if _session is session:
            _session = None


def register_live(session_id: str, handle: LiveTarget) -> None:
    with _lock:
        _live[session_id] = handle


def forget_live(session_id: str) -> None:
    with _lock:
        _live.pop(session_id, None)


def live_for(session_id: str) -> LiveTarget | None:
    """The live session `session_id` names (`LiveSession` or `LiveAsk`), or None."""
    with _lock:
        return _live.get(session_id)

"""What a live review (`live.py`) and a live ask (`live_ask.py`) share.

Its own module because `ask.py` starts a live ask while `live.py` imports `review.py`,
which imports `ask.py`: the shared parts cannot sit in `live.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .listener import LiveHandler

__all__ = ["POLL_SECONDS", "LiveError", "Reply", "Tab"]

# How long a `live` wait sleeps between checks for abandonment.
POLL_SECONDS = 0.5


class LiveError(ValueError):
    """A `live` call the session cannot accept."""


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

"""Helpers several test modules share."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message

from text_wall_breaker.listener import LiveHandler
from text_wall_breaker.live import LiveSession
from text_wall_breaker.review import Page, ReviewRequest


@dataclass(frozen=True)
class Reply:
    status: int
    headers: Message
    body: bytes


def fetch(
    url: str, payload: object = None, *, raw: bytes | None = None, timeout: float = 5
) -> Reply:
    """GET `url`, or POST `payload` as JSON (or `raw` as is) in a `text/plain` body.

    The response is closed before this returns, so no socket outlives the call. An error
    status is returned like any other, not raised.
    """
    data = raw if raw is not None else None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(  # noqa: S310 — tests only reach 127.0.0.1
        url,
        data=data,
        headers={"Content-Type": "text/plain"},
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return Reply(response.status, response.headers, response.read())
    except urllib.error.HTTPError as error:
        with error:
            return Reply(error.code, error.headers, error.read())


def wait_until(condition: Callable[[], bool], timeout: float = 5) -> None:
    """Return once `condition()` holds; fail the test if it never does in `timeout` s."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition never held")
        time.sleep(0.01)


DIFF = """--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""


class FakeSession:
    """The listener a live review session takes over, recording what it is asked to do."""

    def __init__(self):
        self.live: LiveHandler | None = None
        self.generation = 1
        self.published = []
        self.closed = False
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

    def close(self):
        self.closed = True


def make_live_review(diff: str = DIFF) -> tuple[FakeSession, LiveSession]:
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

"""Single-shot loopback listener for one `ask` round trip.

The page is opened from a `file://` URL, so it cannot talk back to the agent except over
the network. This binds an ephemeral port on 127.0.0.1 *before* the page is written, so
the port and a per-call token can be baked into the page's submit URL. The page POSTs
once; this returns that submission and closes.

The token is the whole of the authentication. A `file://` page reports its origin as
`null`, so an Origin check cannot distinguish our page from any other local page that
guessed the port — the unguessable token in the query string is what stands in for it.
This mirrors the contract in no-text-wall's `INTERACTIVE.md`, which the page's submit
script is adapted from.

The timeout is an *idle* timeout, bumped by any request the handler sees, including the
probe the page fires on load. That matters: the window the operator gets to answer in
should start when the browser actually renders the page, not when `xdg-open` returned.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

__all__ = ["LoopbackListener", "Submission"]

# Bodies larger than this are rejected unread. Nothing legitimate approaches it — a
# submission is a handful of option ids and a free-text box.
_MAX_BODY_BYTES = 1 << 20

# How long a single handle_request() blocks before the loop re-checks the deadline.
_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class Submission:
    """One accepted, token-matched POST from the page."""

    kind: str
    data: dict[str, Any]


class _Server(HTTPServer):
    """An HTTPServer that carries the listener its handler reports to."""

    listener: LoopbackListener


class _Handler(BaseHTTPRequestHandler):
    server_version = "pretty-prompt/1"

    # Signature fixed by BaseHTTPRequestHandler; neither argument is used because the
    # whole point of the override is to drop the access log.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: ARG002
        # stdout is the MCP stdio transport. Anything written there corrupts the
        # protocol stream, so the access log is dropped entirely.
        return

    @property
    def listener(self) -> LoopbackListener:
        return cast("_Server", self.server).listener

    def _drain_body(self) -> bytes:
        # Always read the body, even on a path about to reject: leaving it on the wire
        # desyncs the keep-alive connection and breaks the *next* request with a 400.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > _MAX_BODY_BYTES:
            return b""
        return self.rfile.read(length)

    def _respond(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Without an explicit allow-origin the browser treats the response as opaque and
        # the page's `res.ok` check can never see it — so a rejected token would look
        # identical to a successful submit from inside the page.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        listener = self.listener
        listener.note_activity()
        raw = self._drain_body()

        parts = urlsplit(self.path)
        if parts.path != "/submit":
            self._respond(404)
            return

        token = parse_qs(parts.query).get("t", [""])[0]
        if not listener.token_matches(token):
            self._respond(403, b'{"error":"bad token"}')
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, b'{"error":"invalid json"}')
            return
        if not isinstance(payload, dict):
            self._respond(400, b'{"error":"expected an object"}')
            return

        kind = payload.get("kind")
        if kind == "probe":
            # The page fires this on load to prove the token and CORS response are
            # readable. It bumps the idle deadline but must not consume the one
            # submission this listener exists to collect.
            self._respond(204)
            return
        if kind not in ("submit", "cancel"):
            self._respond(400, b'{"error":"unknown kind"}')
            return

        data = payload.get("data")
        self._respond(200, b'{"ok":true}')
        listener.deliver(Submission(kind=kind, data=data if isinstance(data, dict) else {}))


class LoopbackListener:
    """Binds a loopback port now, waits for one submission later.

    Used as a context manager so the socket is closed on every exit path, including the
    timeout and a cancelled tool call.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._submission: Submission | None = None
        self._last_activity = time.monotonic()
        # Port 0 lets the OS pick a free port, which removes the pick-then-race that
        # choosing a port ourselves would introduce.
        self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.timeout = _POLL_SECONDS
        self._httpd.listener = self

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def token_matches(self, token: str) -> bool:
        return secrets.compare_digest(token, self._token)

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    def deliver(self, submission: Submission) -> None:
        self._submission = submission

    def wait(self, timeout_seconds: float) -> Submission | None:
        """Block until a submission arrives, or `timeout_seconds` pass with no traffic.

        Returns None on timeout. Blocking, so callers on an event loop run this in a
        worker thread.
        """
        self.note_activity()
        while self._submission is None:
            if time.monotonic() > self._last_activity + timeout_seconds:
                return None
            self._httpd.handle_request()
        return self._submission

    def close(self) -> None:
        self._httpd.server_close()

    def __enter__(self) -> LoopbackListener:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

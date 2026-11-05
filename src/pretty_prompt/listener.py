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

There is no deadline for an answer. A call ends when the page is *abandoned*: it sends
nothing for ABANDON_SECONDS. Every request the handler sees bumps that clock, and an open
page sends a heartbeat every few seconds (hidden or not), so only a closed tab, a dead
browser or a sleeping machine stops it. The clock starts when the page first renders, not
when `xdg-open` returned.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlsplit

from .page import save_theme

__all__ = ["LoopbackListener", "SessionListener", "Submission"]

# Extensions `GET /asset` (see `SessionListener.read_asset`) will actually read and
# serve, each mapped to the Content-Type it's served as. A closed set for the same
# reason `markdown._SAFE_SCHEMES` is one: the route reads whatever local path the page
# asks for, gated only by the session token, so it must not double as a way to read an
# arbitrary file off disk just because it happens to sit next to a real screenshot.
_ASSET_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

# Comfortably above a full-resolution screenshot, the actual use case this exists for;
# not so high that a bad path turns the route into a memory hog.
_MAX_ASSET_BYTES = 25 * 1024 * 1024

# Bodies larger than this are rejected unread. Nothing legitimate approaches it — a
# submission is a handful of option ids and a free-text box.
_MAX_BODY_BYTES = 1 << 20

# How long a single handle_request() blocks before the loop re-checks the deadline.
_POLL_SECONDS = 0.5

# A page that hasn't loaded this long after its tab was opened never will: the browser
# didn't open it, or opened it somewhere the user can't see. Long enough for a cold
# browser start. The caller reports that as undelivered instead of waiting out its whole
# wait for an answer that can't come.
DELIVERY_SECONDS = 30.0

# How long a page that was loaded may stay silent before its call is given up as
# abandoned. The page's heartbeat is every 15 s, but a browser throttles a background tab's
# timers to about once a minute, so this must stay well above 60 s or a tab merely sitting
# in another window would count as closed.
ABANDON_SECONDS = 180.0

# An open tab on the shared session reloads for a new page within ~1.5 s: it polls
# /status. One that hasn't after this long was closed (or is an unanswered page that
# stopped polling), so a new tab is opened instead of publishing into nothing.
REUSE_GRACE_SECONDS = 4.0


@dataclass(frozen=True)
class Submission:
    """One accepted, token-matched POST from the page."""

    kind: str
    data: dict[str, Any]


def _handle_submit_payload(
    raw: bytes,
    respond: Callable[[int, bytes], None],
    deliver: Callable[[Submission], None],
    progress: Callable[[dict[str, Any]], None] | None = None,
    live: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]] | None = None,
) -> None:
    """Parse one POST /submit body and act on it via `respond`/`deliver`.

    Shared between `_Handler` (one listener, one submission) and `_SessionHandler` (one
    listener, many submissions over its life) — a probe, a theme change, or an answer
    means the same thing to either one; only what "deliver" does with an answer differs.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        respond(400, b'{"error":"invalid json"}')
        return
    if not isinstance(payload, dict):
        respond(400, b'{"error":"expected an object"}')
        return

    kind = payload.get("kind")
    if kind == "probe":
        # The page fires this on load to prove the token and CORS response are
        # readable. It bumps the idle deadline but must not consume the one
        # submission this listener exists to collect.
        respond(204, b"")
        return
    if kind == "theme":
        # The page's toggle reports a change here so the *next* page this process
        # serves can start in it — see page.py's save_theme/read_saved_theme. Never
        # consumes the one submission, the same as a probe.
        data = payload.get("data")
        theme = data.get("theme") if isinstance(data, dict) else None
        if isinstance(theme, str):
            save_theme(theme)
        respond(204, b"")
        return
    if kind == "progress":
        # The page reporting the answers entered so far, for a `resume` draft if the
        # call ends without an answer (see SessionListener's `progress`). Never consumes
        # the submission; a listener with nowhere to keep it just acknowledges it.
        data = payload.get("data")
        if progress is not None and isinstance(data, dict):
            progress(data)
        respond(204, b"")
        return
    if kind == "live":
        # An operator event of a live session (see live.LiveSession.handle); answered
        # with JSON and never the one submission.
        data = payload.get("data")
        if live is None or not isinstance(data, dict):
            respond(409, b'{"error":"no live session"}')
            return
        status, body = live(data)
        respond(status, json.dumps(body).encode("utf-8"))
        return
    # "submit"/"cancel"/"interrupt" are ask's answer kinds; "acknowledge" is show's —
    # see show.run_show; "live_start" is review's Start live session. All are "the one
    # submission this listener exists to collect", handled identically from here on;
    # only the caller distinguishes them by `Submission.kind`.
    if kind not in ("submit", "cancel", "interrupt", "acknowledge", "live_start"):
        respond(400, b'{"error":"unknown kind"}')
        return

    data = payload.get("data")
    # Stored before the page is told it arrived, so "ok" never outruns the answer.
    deliver(Submission(kind=kind, data=data if isinstance(data, dict) else {}))
    respond(200, b'{"ok":true}')


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

        listener.mark_seen()
        _handle_submit_payload(raw, self._respond, listener.deliver, listener.set_progress)


class LoopbackListener:
    """Binds a loopback port now, waits for one submission later.

    Used as a context manager so the socket is closed on every exit path, including the
    abandonment and a cancelled tool call.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._submission: Submission | None = None
        self._progress: dict[str, Any] | None = None
        self._last_activity = time.monotonic()
        self._seen = False
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

    def set_progress(self, data: dict[str, Any]) -> None:
        self._progress = data

    def progress(self) -> dict[str, Any] | None:
        """The answer the page last reported before it ended, for resuming the ask."""
        return self._progress

    def mark_seen(self) -> None:
        self._seen = True

    @property
    def page_seen(self) -> bool:
        """Whether the page has been in touch at all (its load probe, at the latest)."""
        return self._seen

    def wait(self) -> Submission | None:
        """Block until a submission arrives, or the page is abandoned.

        Returns None once ABANDON_SECONDS pass with no traffic, and early, after
        DELIVERY_SECONDS, if the page never loaded; `page_seen` tells the two apart.
        Blocking, so callers on an event loop run this in a worker thread.
        """
        self.note_activity()
        started = time.monotonic()
        while self._submission is None:
            now = time.monotonic()
            if now > self._last_activity + ABANDON_SECONDS:
                return None
            if not self._seen and now > started + DELIVERY_SECONDS:
                return None
            self._httpd.handle_request()
        return self._submission

    def close(self) -> None:
        self._httpd.server_close()

    def __enter__(self) -> LoopbackListener:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _SessionServer(HTTPServer):
    """An HTTPServer that carries the session its handler reports to."""

    session: SessionListener


class _SessionHandler(BaseHTTPRequestHandler):
    server_version = "pretty-prompt/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: ARG002
        return  # see _Handler.log_message

    @property
    def session(self) -> SessionListener:
        return cast("_SessionServer", self.server).session

    def _drain_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > _MAX_BODY_BYTES:
            return b""
        return self.rfile.read(length)

    def _respond(
        self, status: int, body: bytes = b"", content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _token_ok(self, query: str) -> bool:
        return self.session.token_matches(parse_qs(query).get("t", [""])[0])

    def do_GET(self) -> None:
        session = self.session
        parts = urlsplit(self.path)
        if not self._token_ok(parts.query):
            self._respond(403)
            return
        session.note_activity()

        if parts.path in ("", "/"):
            page = session.serve_page()
            self._respond(200, page, content_type="text/html; charset=utf-8")
            return
        if parts.path == "/status":
            body = json.dumps(session.status()).encode("utf-8")
            self._respond(200, body)
            return
        if parts.path == "/live":
            restore = parse_qs(parts.query).get("restore", [""])[0] == "1"
            live = session.live
            if live is None:
                self._respond(200, b'{"session":null}')
                return
            self._respond(200, json.dumps(live.poll(restore=restore)).encode("utf-8"))
            return
        if parts.path == "/asset":
            path = parse_qs(parts.query).get("path", [""])[0]
            status, body, content_type = session.read_asset(path)
            self._respond(status, body, content_type=content_type or "application/octet-stream")
            return
        self._respond(404)

    def do_POST(self) -> None:
        session = self.session
        session.note_activity()
        raw = self._drain_body()

        parts = urlsplit(self.path)
        if parts.path != "/submit":
            self._respond(404)
            return
        if not self._token_ok(parts.query):
            self._respond(403, b'{"error":"bad token"}')
            return

        _handle_submit_payload(
            raw, self._respond, session.deliver, session.set_progress, session.handle_live
        )


class LiveHandler(Protocol):
    """What a live review session (live.LiveSession) offers this listener."""

    def poll(self, *, restore: bool) -> dict[str, Any]: ...
    def handle(self, data: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def on_progress(self, data: dict[str, Any]) -> None: ...
    def on_submission(self, submission: Submission) -> None: ...


class SessionListener:
    """A loopback server that outlives a single `ask` call, so the same browser tab can
    answer several questions instead of a new one opening for each.

    Unlike `LoopbackListener` (one bind, one submission, then closed), this binds once
    and keeps a background thread servicing it for as long as the process runs. `ask.py`
    calls `publish()` with each new question, already-open tab notices the change itself
    — the page polls `GET /status` once it is done with the question it has, and reloads
    when the generation number moves, landing on `GET /` for the new one (see page.py's
    `startReusePolling`). Concurrent `ask` calls cannot share one tab without one
    clobbering the other's in-progress question, so only one caller may hold this at a
    time — see `try_claim`/`release` — and a caller that can't claim it falls back to its
    own one-off `LoopbackListener` tab instead of blocking on this one.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._state_lock = threading.Lock()
        self._busy_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._page = b""
        self._generation = 0
        # The generation a tab last loaded via GET /, which is how `show_in_tab` knows a
        # published page actually reached a browser.
        self._served_generation = 0
        self._submission: Submission | None = None
        self._answered = threading.Event()
        # Whether a call still waits on the current page. Reported by GET /status, so a
        # tab left open after its call was abandoned can say so instead of taking answers
        # nobody will read.
        self._open = False
        # The latest answers-so-far the page reported, for a `resume` draft. Reset by
        # publish().
        self._progress: dict[str, Any] | None = None
        self.tab_opened = False
        # The last `show` published here without waiting on it, as {"title", "state"}
        # ("unacknowledged" until the Acknowledge click arrives). None once anything else
        # is published. `_last_poll` is when the tab last asked GET /status, which is how
        # `holds_unread_report` tells a report still on screen from one whose tab closed.
        self._report: dict[str, str] | None = None
        self._last_poll = 0.0
        # The live review session holding this tab, if any; it holds the claim too.
        self.live: LiveHandler | None = None
        # Port 0 lets the OS pick a free port, same reasoning as LoopbackListener.
        self._httpd = _SessionServer(("127.0.0.1", 0), _SessionHandler)
        self._httpd.session = self
        # serve_forever(), not handle_request() in a loop: unlike LoopbackListener this
        # has to keep answering GET /status between `ask` calls, while nothing is
        # blocked waiting on it — there may be no `run_ask` call in flight at all.
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def token(self) -> str:
        return self._token

    def token_matches(self, token: str) -> bool:
        return secrets.compare_digest(token, self._token)

    def note_activity(self) -> None:
        with self._state_lock:
            self._last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since the tab last sent anything; a live session's abandonment clock."""
        with self._state_lock:
            return time.monotonic() - self._last_activity

    def handle_live(self, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        live = self.live
        if live is None:
            return 409, {"error": "no live session"}
        return live.handle(data)

    def try_claim(self) -> bool:
        """Claim exclusive use of this session for one call. Never blocks.

        Refuses, like a busy session, while an unread `show` report is on screen. The
        caller then falls back to its own one-off tab, so no call, a newer `show`
        included, wipes a report the user is still reading.
        """
        if not self._busy_lock.acquire(blocking=False):
            return False
        if self.holds_unread_report():
            self._busy_lock.release()
            return False
        return True

    def holds_unread_report(self) -> bool:
        """Whether an unacknowledged `show` is still open in a live tab of this session."""
        with self._state_lock:
            if self._report is None or self._report["state"] != "unacknowledged":
                return False
            return time.monotonic() - self._last_poll <= REUSE_GRACE_SECONDS

    def mark_report(self, title: str) -> None:
        """Record that the page just published is a `show` report awaiting Acknowledge."""
        with self._state_lock:
            self._report = {"title": title, "state": "unacknowledged"}
            self._last_poll = time.monotonic()

    def report_status(self) -> dict[str, Any]:
        """The last report's state, for the `show_status` tool.

        `state` is "none" (no report was shown here, or something else replaced it),
        "unacknowledged" or "acknowledged".
        """
        with self._state_lock:
            if self._report is None:
                return {"state": "none"}
            return {"state": self._report["state"], "title": self._report["title"]}

    def release(self) -> None:
        self._busy_lock.release()

    @property
    def page_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self._token}"

    @property
    def asset_base_url(self) -> str:
        """Where the page can fetch a local image through, instead of a `file://` URL.

        Passed to `markdown.render_markdown`/`render_inline` as `asset_base_url` — see
        `_image_src` there for why a page this session serves (over `http://`) needs
        this instead of the `file://` URL an agent wrote working as-is.
        """
        return f"http://127.0.0.1:{self.port}/asset?t={self._token}"

    def read_asset(self, path: str) -> tuple[int, bytes, str]:
        """Read a local image file for `GET /asset`, returning (status, body, content_type).

        `path` is the absolute filesystem path a `file://` image URL held (see
        `markdown._image_src`) — this is what stands in for the browser loading it
        directly, which it refuses to do from an `http://` document. Restricted to a
        closed set of image extensions and a size cap (see `_ASSET_CONTENT_TYPES`/
        `_MAX_ASSET_BYTES`): the token gates who can reach this route at all, the same
        as `/submit`, but that's no reason to let it double as an arbitrary local file
        read.
        """
        candidate = Path(path)
        content_type = _ASSET_CONTENT_TYPES.get(candidate.suffix.lower())
        if content_type is None or not candidate.is_absolute():
            return 404, b"", ""
        try:
            if not candidate.is_file():
                return 404, b"", ""
            if candidate.stat().st_size > _MAX_ASSET_BYTES:
                return 413, b"", ""
            data = candidate.read_bytes()
        except OSError:
            return 404, b"", ""
        return 200, data, content_type

    def current_page(self) -> tuple[bytes, int]:
        with self._state_lock:
            return self._page, self._generation

    def serve_page(self) -> bytes:
        """The current page, for a tab loading it: records that it reached one."""
        with self._state_lock:
            self._served_generation = self._generation
            return self._page

    def _wait_served(self, generation: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            with self._state_lock:
                if self._served_generation >= generation:
                    return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)

    def show_in_tab(self, generation: int, open_tab: Callable[[str], None]) -> bool:
        """Get `generation` in front of the user, returning False if no tab loads it.

        Reuses the tab opened earlier if it picks the page up by itself, which an open
        tab does within REUSE_GRACE_SECONDS. Otherwise that tab is gone, so `open_tab`
        opens a new one. Trusting `tab_opened` alone, as this once did, published every
        later page into a tab the user had already closed. A tab the browser throttles
        hard in the background may miss the grace and get a second tab beside it, which
        is the cheaper mistake.
        """
        if self.tab_opened and self._wait_served(generation, REUSE_GRACE_SECONDS):
            return True
        open_tab(self.page_url)
        self.tab_opened = True
        return self._wait_served(generation, DELIVERY_SECONDS)

    def publish(self, build_html: Callable[[int], str]) -> int:
        """Swap in the next question's page, built from the generation it will carry.

        `build_html` gets the new generation number so it can be baked into the page's
        own `GENERATION` — a page it matches means it's already showing this content, no
        reload needed; anything else means a newer question is waiting. Returns the new
        generation, for `show_in_tab`.
        """
        with self._state_lock:
            self._generation += 1
            self._page = build_html(self._generation).encode("utf-8")
            self._submission = None
            self._open = True
            self._progress = None
            self._report = None
            generation = self._generation
        self._answered.clear()
        self.note_activity()
        return generation

    def end_question(self) -> None:
        """Record that no call waits on the current page any more."""
        with self._state_lock:
            self._open = False

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            self._last_poll = time.monotonic()
            return {
                "generation": self._generation,
                "open": self._open,
            }

    def set_progress(self, data: dict[str, Any]) -> None:
        with self._state_lock:
            self._progress = data
        live = self.live
        if live is not None:
            live.on_progress(data)

    def progress(self) -> dict[str, Any] | None:
        """The answers the page last reported before it ended, for resuming the ask."""
        with self._state_lock:
            return self._progress

    def deliver(self, submission: Submission) -> None:
        with self._state_lock:
            self._submission = submission
            if submission.kind == "acknowledge" and self._report is not None:
                self._report["state"] = "acknowledged"
        live = self.live
        if live is not None:
            live.on_submission(submission)
        self._answered.set()

    def wait_for_answer(self) -> Submission | None:
        """Block until the current question is answered, or the tab is abandoned.

        Abandoned means ABANDON_SECONDS without any GET/POST traffic (see note_activity).
        Polls in `_POLL_SECONDS` slices so traffic that moves the deadline is re-checked
        promptly instead of only after the original window would have lapsed.
        """
        self.note_activity()
        while True:
            if self._answered.wait(timeout=_POLL_SECONDS):
                with self._state_lock:
                    return self._submission
            with self._state_lock:
                idle_for = time.monotonic() - self._last_activity
            if idle_for > ABANDON_SECONDS:
                return None

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

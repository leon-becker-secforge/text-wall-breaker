"""One `ask` round trip: validate, write the page, open it, wait for the answer.

`run_ask` is synchronous and blocking by design — it owns a socket and a browser tab for
its whole duration. The MCP tool runs it in a worker thread so the server's event loop
stays free, which is what lets two overlapping `ask` calls each open their own tab with
their own port and token.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .listener import LoopbackListener
from .page import ACCENT_ENV_VAR, InvalidAccent, build_page, parse_accent

__all__ = ["AskRequest", "BrowserUnavailable", "InvalidAsk", "Option", "run_ask"]

# xdg-open hands the file to the desktop's browser and returns immediately. If it has
# not returned by now something is wrong with the desktop session, and failing is better
# than blocking for the caller's whole timeout with no window on screen.
_OPEN_TIMEOUT_SECONDS = 20.0


class InvalidAsk(ValueError):
    """The request cannot produce an answerable page."""


class BrowserUnavailable(RuntimeError):
    """The page was written but no browser could be opened for it."""


class Option(BaseModel):
    """One choice on the page."""

    id: Annotated[
        str,
        Field(description="Stable identifier returned in `selected`. Never shown to the user."),
    ]
    label: Annotated[
        str,
        Field(description="Short plain-text label. Not Markdown, not rendered."),
    ]
    body: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional longer Markdown shown under the label, same role as "
            "AskUserQuestion's description.",
        ),
    ] = None
    recommended: Annotated[
        bool,
        Field(
            description="Pre-expand this option's body on the page, since the user is "
            "reading it first. Does not pre-select it — the user still has to check it "
            "themselves. Has no visible effect if body is not set, or is short enough "
            "to be shown inline rather than behind a click.",
        ),
    ] = False


class AskRequest(BaseModel):
    """A validated `ask` call."""

    question: str
    options: list[Option] = []
    multi_select: bool = False
    allow_free_text: bool = True
    timeout_seconds: float = 300.0

    def validated(self) -> AskRequest:
        """Return self, or raise `InvalidAsk` if the page would be unanswerable."""
        if not self.question.strip():
            raise InvalidAsk("question is empty")
        if not self.options and not self.allow_free_text:
            raise InvalidAsk(
                "options is empty and allow_free_text is false, so the page would have "
                "no way to answer — supply options, or set allow_free_text"
            )
        seen: set[str] = set()
        for option in self.options:
            if not option.id.strip():
                raise InvalidAsk("every option needs a non-empty id")
            if not option.label.strip():
                raise InvalidAsk(f"option {option.id!r} has an empty label")
            if option.id in seen:
                raise InvalidAsk(f"duplicate option id {option.id!r}")
            seen.add(option.id)
        if self.timeout_seconds <= 0:
            raise InvalidAsk("timeout_seconds must be positive")
        return self


def open_in_browser(path: Path) -> None:
    """Hand `path` to the desktop's browser, raising `BrowserUnavailable` if it refuses."""
    opener = shutil.which("xdg-open")
    if opener is None:
        raise BrowserUnavailable("xdg-open is not on PATH")
    try:
        # Fixed argv, no shell, and the only caller-influenced part is a path this
        # process just wrote into its own temp directory.
        result = subprocess.run(  # noqa: S603 — fixed executable, no shell
            [opener, str(path)],
            capture_output=True,
            text=True,
            timeout=_OPEN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserUnavailable("xdg-open did not return") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise BrowserUnavailable(f"xdg-open failed: {detail}")


def _resolve_accent() -> str | None:
    """Read `PRETTY_PROMPT_ACCENT`, or None if it's unset or unusable.

    `server.main` validates the same variable at startup and refuses to run rather than
    let a typo pass silently — this is the fallback for the case that check can't cover
    (`run_ask` called directly, as the tests do, without going through `main`), where
    falling back quietly beats crashing every `ask` over one bad environment variable.
    """
    value = os.environ.get(ACCENT_ENV_VAR)
    if not value:
        return None
    try:
        parse_accent(value)
    except InvalidAccent:
        return None
    return value


def run_ask(request: AskRequest) -> dict[str, Any]:
    """Open the page for `request` and block until it is answered, cancelled or idle.

    Returns exactly one of `{"selected", "free_text"}`, `{"cancelled": True}` or
    `{"timed_out": True}`.
    """
    request = request.validated()
    token = secrets.token_hex(16)

    # Bind before writing: the page needs the port baked into its submit URL, and a port
    # chosen any other way is a race against whatever else is starting on this machine.
    with LoopbackListener(token) as listener:
        submit_url = f"http://127.0.0.1:{listener.port}/submit?t={token}"
        html = build_page(
            question=request.question,
            options=[option.model_dump() for option in request.options],
            multi_select=request.multi_select,
            allow_free_text=request.allow_free_text,
            submit_url=submit_url,
            timeout_seconds=request.timeout_seconds,
            accent=_resolve_accent(),
        )

        # One directory per call, removed on every exit path. Nothing outlives the call
        # it belongs to.
        directory = Path(tempfile.mkdtemp(prefix="pretty-prompt-"))
        try:
            page = directory / "ask.html"
            page.write_text(html, encoding="utf-8")
            open_in_browser(page)
            submission = listener.wait(request.timeout_seconds)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    if submission is None:
        return {"timed_out": True}
    if submission.kind == "cancel":
        return {"cancelled": True}

    # Ids are filtered against the request rather than trusted: the page is the only
    # thing that should ever POST here, but the answer is what the agent acts on.
    known = {option.id for option in request.options}
    raw_selected = submission.data.get("selected")
    selected = (
        [value for value in raw_selected if value in known]
        if isinstance(raw_selected, list)
        else []
    )
    free_text = submission.data.get("free_text")
    return {
        "selected": selected,
        "free_text": free_text.strip() if isinstance(free_text, str) else "",
    }

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
from .page import ACCENT_ENV_VAR, InvalidAccent, build_multi_page, build_page, parse_accent

__all__ = [
    "AskRequest",
    "BrowserUnavailable",
    "InvalidAsk",
    "Option",
    "Question",
    "run_ask",
]

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


class Question(BaseModel):
    """One question on a page — the single-question shape, reused for `questions`."""

    question: str
    options: list[Option] = []
    multi_select: bool = False
    allow_free_text: bool = True

    def validated(self, *, label: str) -> Question:
        """Return self, or raise `InvalidAsk` naming `label` if unanswerable."""
        if not self.question.strip():
            raise InvalidAsk(f"{label}: question is empty")
        if not self.options and not self.allow_free_text:
            raise InvalidAsk(
                f"{label}: options is empty and allow_free_text is false, so it would "
                "have no way to answer — supply options, or set allow_free_text"
            )
        seen: set[str] = set()
        for option in self.options:
            if not option.id.strip():
                raise InvalidAsk(f"{label}: every option needs a non-empty id")
            if not option.label.strip():
                raise InvalidAsk(f"{label}: option {option.id!r} has an empty label")
            if option.id in seen:
                raise InvalidAsk(f"{label}: duplicate option id {option.id!r}")
            seen.add(option.id)
        return self


class AskRequest(BaseModel):
    """A validated `ask` call.

    Either the top-level `question`/`options`/`multi_select`/`allow_free_text` (one
    question) or `questions` (several, on one page) is set — never both. See
    `page.build_multi_page` for why the multi-question path is a separate prototype
    rather than a generalisation of the single-question one.
    """

    question: str = ""
    options: list[Option] = []
    multi_select: bool = False
    allow_free_text: bool = True
    questions: list[Question] | None = None
    timeout_seconds: float = 300.0

    def validated(self) -> AskRequest:
        """Return self, or raise `InvalidAsk` if the page would be unanswerable."""
        if self.timeout_seconds <= 0:
            raise InvalidAsk("timeout_seconds must be positive")
        if self.questions is not None:
            if self.question.strip() or self.options:
                raise InvalidAsk("set either question/options or questions, not both")
            if not self.questions:
                raise InvalidAsk("questions is empty")
            for index, question in enumerate(self.questions):
                question.validated(label=f"questions[{index}]")
            return self
        self._as_single_question().validated(label="question")
        return self

    def _as_single_question(self) -> Question:
        return Question(
            question=self.question,
            options=self.options,
            multi_select=self.multi_select,
            allow_free_text=self.allow_free_text,
        )

    @property
    def steps(self) -> list[Question]:
        """The page's questions: `questions` as given, or `question` as a list of one."""
        return self.questions if self.questions is not None else [self._as_single_question()]


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


def _extract_answer(step: Question, raw: object) -> dict[str, Any]:
    """One `{selected, free_text}` from a page's answer for `step`.

    Ids are filtered against `step` rather than trusted: the page is the only thing
    that should ever POST here, but the answer is what the agent acts on.
    """
    known = {option.id for option in step.options}
    raw_selected = raw.get("selected") if isinstance(raw, dict) else None
    selected = (
        [value for value in raw_selected if value in known]
        if isinstance(raw_selected, list)
        else []
    )
    free_text = raw.get("free_text") if isinstance(raw, dict) else None
    return {
        "selected": selected,
        "free_text": free_text.strip() if isinstance(free_text, str) else "",
    }


def run_ask(request: AskRequest) -> dict[str, Any]:
    """Open the page for `request` and block until it is answered, cancelled or idle.

    Returns `{"cancelled": True}` or `{"timed_out": True}` either way. Otherwise, for a
    single question (the `question` shorthand), `{"selected", "free_text"}`; for
    `questions`, `{"answers": [{"selected", "free_text"}, ...]}`, one entry per question,
    in order.
    """
    request = request.validated()
    steps = request.steps
    token = secrets.token_hex(16)

    # Bind before writing: the page needs the port baked into its submit URL, and a port
    # chosen any other way is a race against whatever else is starting on this machine.
    with LoopbackListener(token) as listener:
        submit_url = f"http://127.0.0.1:{listener.port}/submit?t={token}"
        if request.questions is None:
            step = steps[0]
            html = build_page(
                question=step.question,
                options=[option.model_dump() for option in step.options],
                multi_select=step.multi_select,
                allow_free_text=step.allow_free_text,
                submit_url=submit_url,
                timeout_seconds=request.timeout_seconds,
                accent=_resolve_accent(),
            )
        else:
            html = build_multi_page(
                questions=[
                    {
                        "question": step.question,
                        "options": [option.model_dump() for option in step.options],
                        "multi_select": step.multi_select,
                        "allow_free_text": step.allow_free_text,
                    }
                    for step in steps
                ],
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

    if request.questions is None:
        return _extract_answer(steps[0], submission.data)

    raw_answers = submission.data.get("answers")
    answers = [
        _extract_answer(
            step,
            raw_answers[index]
            if isinstance(raw_answers, list) and index < len(raw_answers)
            else None,
        )
        for index, step in enumerate(steps)
    ]
    return {"answers": answers}

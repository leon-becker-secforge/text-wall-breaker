"""One `ask` round trip: validate, write the page, open it, wait for the answer.

`run_ask` is synchronous and blocking by design — it owns a socket and, ordinarily, a
browser tab for its whole duration. The MCP tool runs it in a worker thread so the
server's event loop stays free.

Two ways a call gets its tab, tried in that order:

  1. Reuse the process's one shared `SessionListener` tab (see `session.py`), if nothing
     else is using it right now (see `try_claim`). `show` calls reuse the very same
     session, so an `ask` and a `show` in the same process land in one tab between them.
     The already-open tab notices the new question itself and reloads — see `page.py`'s
     `startReusePolling` — so the person answering doesn't get a new tab per question.
  2. Fall back to a one-off `LoopbackListener` and its own freshly opened tab, exactly as
     every call worked before tab reuse existed. This is what makes two overlapping `ask`
     calls safe: the second one can't wait for the first to free the shared tab, so it
     gets its own instead of blocking or colliding with it.

A streamed ask (`more_coming`) spans several calls instead: the first opens the page with
the questions written so far and returns an `ask_id` at once, each later call with that
id appends its questions to the open page, and the call that says nothing more is coming
waits for the answers. It needs a listener that outlives a call, so it holds the shared
session for the whole stream, or, if that is busy, gets a `SessionListener` of its own
rather than a `LoopbackListener`.

A cancelled or timed-out ask that had answers entered returns a `resume` id: the pages
report every change as they go, the server keeps the last report as a draft, and a call
with `resume` reopens the same questions with those answers prefilled.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .listener import LoopbackListener, SessionListener, Submission
from .markdown import LONG_QUESTION_CHARS, first_paragraph
from .opener import BrowserUnavailable, find_opener
from .page import (
    ACCENT_ENV_VAR,
    InvalidAccent,
    build_multi_page,
    build_page,
    build_step_fragments,
    parse_accent,
    read_saved_theme,
)
from .session import get_shared_session

__all__ = [
    "AskRequest",
    "BrowserUnavailable",
    "InvalidAsk",
    "Option",
    "Question",
    "run_ask",
]

# Whether an answer carries how it was reached: `changed_from` (options picked, then
# abandoned) and, per entry of `questions`, `visits` (how often that step was shown).
# The user is the one being observed, so this is a single switch rather than something
# spread across the page and the result: set it to False and neither field is returned
# nor mentioned in the tool description. `expanded` predates it and is not covered.
SHARE_INTERACTION_SIGNALS = True

# The page never loaded, so nobody saw the question: the agent should fall back at once,
# not treat it like an unanswered `timed_out`. See listener.DELIVERY_SECONDS.
NOT_DELIVERED: dict[str, Any] = {"not_delivered": True}


class InvalidAsk(ValueError):
    """The request cannot produce an answerable page."""


# Short enough that a caveat stays one line on the page. Enforced rather than documented:
# the reason caveats exist is that agents write long when allowed, and a long caveat is
# just a second body that happens to be visible.
CAVEAT_MAX_CHARS = 160


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
    caveat: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional one-line precondition or cost that could flip the "
            "choice, always shown under the label even when body is collapsed. Put "
            "anything that could flip the choice here, not in body. Use sparingly: it "
            "is drawn as a highlighted strip, so a caveat on every option drowns out the "
            f"one that matters. Inline Markdown only, at most {CAVEAT_MAX_CHARS} characters.",
        ),
    ] = None
    body: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional longer Markdown shown under the label, same role as "
            "AskUserQuestion's description.",
        ),
    ] = None
    selected: Annotated[
        bool,
        Field(
            description="Prefill: the option starts checked, for the user to keep or "
            "change. Several per question need multi_select. Not counted as picked in "
            "`changed_from`.",
        ),
    ] = False
    recommended: Annotated[
        bool,
        Field(
            description="The option you lean towards: marked with a Recommended badge, "
            "and its body pre-expanded if it is long enough to sit behind a click. Does "
            "not pre-select it; the user still has to check it themselves. At most one "
            "per question unless multi_select is set. Put the reason in caveat or body.",
        ),
    ] = False


ALLOW_DEFER_DESCRIPTION = (
    'Offer a "Not yet" control, for a question the user may not be ready to settle '
    "because it depends on something still open. A deferred answer comes back with "
    "deferred: true, no selection, and the reason (if any) in free_text. The result "
    "only carries a deferred key when this is set."
)


class Question(BaseModel):
    """One question on a page — the single-question shape, reused for `questions`."""

    id: Annotated[
        str | None,
        Field(
            description="Optional stable identifier, echoed as `id` on this question's "
            "entry in `answers` so a result is matched by id, not position. Unique "
            "within the ask. Never shown to the user."
        ),
    ] = None
    question: str
    options: list[Option] = []
    multi_select: bool = False
    allow_free_text: bool = True
    allow_defer: Annotated[bool, Field(description=ALLOW_DEFER_DESCRIPTION)] = False
    free_text: Annotated[
        str | None,
        Field(description="Prefill: text the free-text box starts with, for the user to edit."),
    ] = None

    def validated(self, *, label: str) -> Question:
        """Return self, or raise `InvalidAsk` naming `label` if unanswerable."""
        if self.id is not None and not self.id.strip():
            raise InvalidAsk(f"{label}: id is empty; omit it or give a non-empty one")
        if not self.question.strip():
            raise InvalidAsk(f"{label}: question is empty")
        if not self.options and not self.allow_free_text:
            raise InvalidAsk(
                f"{label}: options is empty and allow_free_text is false, so it would "
                "have no way to answer — supply options, or set allow_free_text"
            )
        recommended = sum(1 for option in self.options if option.recommended)
        if recommended > 1 and not self.multi_select:
            raise InvalidAsk(
                f"{label}: {recommended} options are recommended but multi_select is "
                "false; recommend at most one"
            )
        prefilled = sum(1 for option in self.options if option.selected)
        if prefilled > 1 and not self.multi_select:
            raise InvalidAsk(
                f"{label}: {prefilled} options are selected but multi_select is false; "
                "prefill at most one"
            )
        seen: set[str] = set()
        for option in self.options:
            if not option.id.strip():
                raise InvalidAsk(f"{label}: every option needs a non-empty id")
            if not option.label.strip():
                raise InvalidAsk(f"{label}: option {option.id!r} has an empty label")
            if option.caveat is not None:
                if "\n" in option.caveat:
                    raise InvalidAsk(
                        f"{label}: option {option.id!r} has a caveat spanning several "
                        "lines; a caveat is one line, so move the rest into body"
                    )
                if len(option.caveat) > CAVEAT_MAX_CHARS:
                    raise InvalidAsk(
                        f"{label}: option {option.id!r} has a caveat of "
                        f"{len(option.caveat)} characters; the limit is {CAVEAT_MAX_CHARS}, "
                        "so move the rest into body"
                    )
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
    allow_defer: bool = False
    questions: list[Question] | None = None
    header: str = ""
    timeout_seconds: float = 300.0
    more_coming: bool = False
    ask_id: str | None = None
    resume: str | None = None
    free_text: str | None = None  # prefill of the single-question shorthand; set by resume only

    def validated(self) -> AskRequest:
        """Return self, or raise `InvalidAsk` if the page would be unanswerable."""
        if self.timeout_seconds <= 0:
            raise InvalidAsk("timeout_seconds must be positive")
        if self.resume is not None:
            if (
                self.question.strip()
                or self.options
                or self.questions is not None
                or self.header.strip()
                or self.more_coming
                or self.ask_id is not None
            ):
                raise InvalidAsk("resume reopens an earlier ask; only timeout_seconds applies")
            return self
        if self.ask_id is not None:
            return self._validated_continuation()
        if self.more_coming and self.questions is None:
            raise InvalidAsk(
                "more_coming needs questions: a streamed ask is several questions sent in chunks"
            )
        if self.header.strip() and self.questions is None:
            raise InvalidAsk(
                "header needs questions: a single question is already Markdown, so put "
                "the context there"
            )
        if self.questions is not None:
            if self.question.strip() or self.options:
                raise InvalidAsk("set either question/options or questions, not both")
            if not self.questions:
                raise InvalidAsk("questions is empty")
            for index, question in enumerate(self.questions):
                question.validated(label=f"questions[{index}]")
            _check_question_ids(self.questions)
            return self
        self._as_single_question().validated(label="question")
        return self

    def _validated_continuation(self) -> AskRequest:
        """The checks for a later call of a streamed ask (`ask_id` set)."""
        if self.question.strip() or self.options:
            raise InvalidAsk("a call with ask_id adds questions, not question/options")
        if self.header.strip():
            raise InvalidAsk("header can only be set on a streamed ask's first call")
        if self.more_coming and not self.questions:
            raise InvalidAsk(
                "more_coming is set but questions is empty; send the next questions, or "
                "leave more_coming unset to wait for the answers"
            )
        for index, question in enumerate(self.questions or []):
            question.validated(label=f"questions[{index}]")
        _check_question_ids(self.questions or [])
        return self

    def _as_single_question(self) -> Question:
        return Question(
            question=self.question,
            options=self.options,
            multi_select=self.multi_select,
            allow_free_text=self.allow_free_text,
            allow_defer=self.allow_defer,
            free_text=self.free_text,
        )

    @property
    def steps(self) -> list[Question]:
        """The page's questions: `questions` as given, or `question` as a list of one."""
        return self.questions if self.questions is not None else [self._as_single_question()]


def open_in_browser(target: Path | str) -> None:
    """Hand `target` to the user's browser, raising `BrowserUnavailable` if it refuses.

    `target` is a `file://`-openable path for a one-shot page, or a `http://127.0.0.1:...`
    URL for a reused-tab session (see `SessionListener`). Which program opens it is
    `opener.find_opener`'s call.
    """
    opener = find_opener()
    if isinstance(opener, str):
        raise BrowserUnavailable(opener)
    opener.open(target)


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


def _check_question_ids(questions: list[Question], taken: set[str] | None = None) -> None:
    """Raise `InvalidAsk` if a question id repeats within `questions` or in `taken`."""
    seen = set(taken or ())
    for index, question in enumerate(questions):
        if question.id is None:
            continue
        if question.id in seen:
            raise InvalidAsk(f"questions[{index}]: duplicate question id {question.id!r}")
        seen.add(question.id)


def _extract_answer(step: Question, raw: object, *, multi: bool = False) -> dict[str, Any]:
    """One `{selected, free_text, expanded}` from a page's answer for `step`.

    Plus `changed_from`, and `visits` when `multi`, unless SHARE_INTERACTION_SIGNALS is
    off.

    Ids are filtered against `step` rather than trusted: the page is the only thing
    that should ever POST here, but the answer is what the agent acts on.
    """
    known = {option.id for option in step.options}

    def _known_ids(key: str) -> list[str]:
        raw_ids = raw.get(key) if isinstance(raw, dict) else None
        return [value for value in raw_ids if value in known] if isinstance(raw_ids, list) else []

    free_text = raw.get("free_text") if isinstance(raw, dict) else None
    answer: dict[str, Any] = {
        **({"id": step.id} if step.id is not None else {}),
        "selected": _known_ids("selected"),
        "free_text": free_text.strip() if isinstance(free_text, str) else "",
        "expanded": _known_ids("expanded"),
    }
    # Only on a step that offered it, so callers who never asked for deferral see the
    # same shape as before. A deferred answer never carries a selection: the page locks
    # the options while deferred, and this holds even if a payload says otherwise.
    if step.allow_defer:
        deferred = isinstance(raw, dict) and raw.get("deferred") is True
        answer["deferred"] = deferred
        if deferred:
            answer["selected"] = []
    if not SHARE_INTERACTION_SIGNALS:
        return answer
    # Set after deferral has emptied `selected`, so a pick abandoned by deferring counts.
    # dict.fromkeys dedupes in first-picked order.
    final = set(answer["selected"])
    changed = dict.fromkeys(value for value in _known_ids("changed_from") if value not in final)
    answer["changed_from"] = list(changed)
    if multi:
        visits = raw.get("visits") if isinstance(raw, dict) else None
        valid = isinstance(visits, int) and not isinstance(visits, bool)
        answer["visits"] = max(visits, 0) if valid else 0
    return answer


def _build_html(
    request: AskRequest,
    steps: list[Question],
    *,
    submit_url: str,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    """The page for `request`, single-question or multi- as it calls for.

    `status_url`/`generation` are only set on the shared-session path — see
    `_run_ask_reusing_tab` — and default to "not reused" for the one-shot path.
    """
    if request.questions is None:
        step = steps[0]
        return build_page(
            question=step.question,
            options=[option.model_dump() for option in step.options],
            multi_select=step.multi_select,
            allow_free_text=step.allow_free_text,
            allow_defer=step.allow_defer,
            free_text_prefill=step.free_text or "",
            submit_url=submit_url,
            timeout_seconds=request.timeout_seconds,
            accent=_resolve_accent(),
            theme=read_saved_theme(),
            status_url=status_url,
            generation=generation,
        )
    return build_multi_page(
        questions=[
            {
                "question": step.question,
                "options": [option.model_dump() for option in step.options],
                "multi_select": step.multi_select,
                "allow_free_text": step.allow_free_text,
                "allow_defer": step.allow_defer,
                "free_text": step.free_text,
            }
            for step in steps
        ],
        header=request.header,
        submit_url=submit_url,
        timeout_seconds=request.timeout_seconds,
        accent=_resolve_accent(),
        theme=read_saved_theme(),
        status_url=status_url,
        generation=generation,
    )


def _extract_answers(steps: list[Question], raw_answers: object) -> list[dict[str, Any]]:
    """One answer per step from a page's `answers` list, padded empty where it's short."""
    return [
        _extract_answer(
            step,
            raw_answers[index]
            if isinstance(raw_answers, list) and index < len(raw_answers)
            else None,
            multi=True,
        )
        for index, step in enumerate(steps)
    ]


# An ask that ended unanswered keeps what the user had entered, so `resume` can reopen it.
# In memory only, and few and short-lived: a draft is for picking an ask back up
# straight away, not an archive.
_DRAFT_TTL_SECONDS = 3600.0
_DRAFT_LIMIT = 20


@dataclass
class _Draft:
    steps: list[Question]
    header: str
    multi: bool
    answers: list[Any]
    saved: float


_drafts: dict[str, _Draft] = {}
_drafts_lock = threading.Lock()


def _save_draft(
    steps: list[Question], header: str, *, multi: bool, progress: dict[str, Any] | None
) -> str | None:
    """Keep the answer a page last reported, returning its `resume` id.

    None when there is nothing worth resuming: no report, or nothing selected or typed.
    """
    raw = (progress or {}).get("answers") if multi else [progress]
    if not isinstance(raw, list) or not any(
        isinstance(entry, dict) and (entry.get("selected") or entry.get("free_text"))
        for entry in raw
    ):
        return None
    now = time.monotonic()
    draft_id = secrets.token_hex(8)
    with _drafts_lock:
        for key in [k for k, d in _drafts.items() if now - d.saved > _DRAFT_TTL_SECONDS]:
            del _drafts[key]
        while len(_drafts) >= _DRAFT_LIMIT:
            del _drafts[next(iter(_drafts))]
        _drafts[draft_id] = _Draft(list(steps), header, multi, raw, now)
    return draft_id


def _resumed_request(request: AskRequest) -> AskRequest:
    """The ask `request.resume` names, reopened with the user's earlier answers prefilled.

    A deferral is not carried over (its reason stays in the free-text box).
    """
    with _drafts_lock:
        draft = _drafts.pop(request.resume or "", None)
    if draft is None or time.monotonic() - draft.saved > _DRAFT_TTL_SECONDS:
        raise InvalidAsk(
            f"no draft with resume id {request.resume!r}: it was already resumed, expired "
            f"after {_DRAFT_TTL_SECONDS / 60:.0f} minutes, or never existed"
        )
    steps = []
    for index, step in enumerate(draft.steps):
        raw = draft.answers[index] if index < len(draft.answers) else None
        answer = _extract_answer(step, raw)
        picked = set(answer["selected"])
        steps.append(
            step.model_copy(
                update={
                    "options": [
                        option.model_copy(update={"selected": option.id in picked})
                        for option in step.options
                    ],
                    "free_text": answer["free_text"] or None,
                }
            )
        )
    if draft.multi:
        return AskRequest(
            questions=steps, header=draft.header, timeout_seconds=request.timeout_seconds
        )
    only = steps[0]
    return AskRequest(
        question=only.question,
        options=only.options,
        multi_select=only.multi_select,
        allow_free_text=only.allow_free_text,
        allow_defer=only.allow_defer,
        free_text=only.free_text,
        timeout_seconds=request.timeout_seconds,
    )


def _unanswered(
    result: dict[str, Any],
    steps: list[Question],
    header: str,
    *,
    multi: bool,
    progress: dict[str, Any] | None,
) -> dict[str, Any]:
    """`result` (cancelled or timed out), plus a `resume` id if there is a draft to resume."""
    draft_id = _save_draft(steps, header, multi=multi, progress=progress)
    return {**result, "resume": draft_id} if draft_id else result


def _answer_from_submission(
    steps: list[Question],
    submission: Submission | None,
    *,
    multi: bool,
    header: str = "",
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if submission is None:
        return _unanswered({"timed_out": True}, steps, header, multi=multi, progress=progress)
    if submission.kind == "cancel":
        return _unanswered({"cancelled": True}, steps, header, multi=multi, progress=progress)
    if not multi:
        return _extract_answer(steps[0], submission.data)
    return {"answers": _extract_answers(steps, submission.data.get("answers"))}


def _run_ask_reusing_tab(
    request: AskRequest, steps: list[Question], session: SessionListener
) -> dict[str, Any]:
    """Answer `request` on `session`'s already-claimed, possibly already-open tab."""
    submit_url = f"http://127.0.0.1:{session.port}/submit?t={session.token}"
    status_url = f"http://127.0.0.1:{session.port}/status?t={session.token}"

    generation = session.publish(
        lambda generation: _build_html(
            request, steps, submit_url=submit_url, status_url=status_url, generation=generation
        )
    )
    # An already-open tab picks up the new content on its own (see page.py's
    # startReusePolling); show_in_tab only opens one if it doesn't.
    if not session.show_in_tab(generation, open_in_browser):
        return NOT_DELIVERED

    try:
        submission = session.wait_for_answer(request.timeout_seconds)
    finally:
        session.end_question()
    return _answer_from_submission(
        steps,
        submission,
        multi=request.questions is not None,
        header=request.header,
        progress=session.progress(),
    )


def _run_ask_with_new_tab(request: AskRequest, steps: list[Question]) -> dict[str, Any]:
    """Answer `request` with its own one-off tab — the original, always-safe behaviour."""
    token = secrets.token_hex(16)

    # Bind before writing: the page needs the port baked into its submit URL, and a port
    # chosen any other way is a race against whatever else is starting on this machine.
    with LoopbackListener(token) as listener:
        submit_url = f"http://127.0.0.1:{listener.port}/submit?t={token}"
        html = _build_html(request, steps, submit_url=submit_url)

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
        if not listener.page_seen:
            return NOT_DELIVERED
        progress = listener.progress()

    return _answer_from_submission(
        steps,
        submission,
        multi=request.questions is not None,
        header=request.header,
        progress=progress,
    )


# A streamed ask holds its tab between calls. One the agent never finishes is given up
# after this long without a call from it, so it can't keep the shared tab forever. Long
# enough for an agent writing a question with a long body; the final call, which waits
# on the user instead, is exempt.
_STREAM_IDLE_SECONDS = 900.0


@dataclass
class _Stream:
    """A streamed ask between its calls: its listener, and every question sent so far."""

    session: SessionListener
    shared: bool  # the process's shared session (released at the end) or its own (closed)
    steps: list[Question]
    header: str
    last_call: float
    waiting: bool = False


_streams: dict[str, _Stream] = {}
_streams_lock = threading.Lock()


def _end_stream(ask_id: str) -> None:
    with _streams_lock:
        stream = _streams.pop(ask_id, None)
    if stream is None:
        return
    stream.session.end_question()
    if stream.shared:
        stream.session.release()
    else:
        stream.session.close()


def _expire_idle_streams() -> None:
    now = time.monotonic()
    with _streams_lock:
        idle = [
            ask_id
            for ask_id, stream in _streams.items()
            if not stream.waiting and now - stream.last_call > _STREAM_IDLE_SECONDS
        ]
    for ask_id in idle:
        _end_stream(ask_id)


def _step_specs(steps: list[Question]) -> list[dict[str, object]]:
    return [
        {
            "question": step.question,
            "options": [option.model_dump() for option in step.options],
            "multi_select": step.multi_select,
            "allow_free_text": step.allow_free_text,
            "allow_defer": step.allow_defer,
            "free_text": step.free_text,
        }
        for step in steps
    ]


def _stream_page(
    stream: _Stream, timeout_seconds: float, *, more_coming: bool
) -> Callable[[int], str]:
    """A `build_html` for `stream`'s session: the whole page, every step sent so far."""
    session = stream.session
    base = f"http://127.0.0.1:{session.port}"
    token = session.token

    def build(generation: int) -> str:
        return build_multi_page(
            questions=_step_specs(stream.steps),
            header=stream.header,
            submit_url=f"{base}/submit?t={token}",
            status_url=f"{base}/status?t={token}",
            steps_url=f"{base}/steps?t={token}",
            more_coming=more_coming,
            timeout_seconds=timeout_seconds,
            accent=_resolve_accent(),
            theme=read_saved_theme(),
            generation=generation,
        )

    return build


def _answers_so_far(stream: _Stream) -> list[dict[str, Any]]:
    """The answers the page last reported, one per question the user has reached."""
    progress = stream.session.progress() or {}
    raw = progress.get("answers")
    reached = len(raw) if isinstance(raw, list) else 0
    return _extract_answers(stream.steps[:reached], raw)


def _start_stream(request: AskRequest) -> dict[str, Any]:
    session = get_shared_session()
    shared = session.try_claim()
    if not shared:
        session = SessionListener(secrets.token_hex(16))
    steps = list(request.questions or [])
    stream = _Stream(
        session=session,
        shared=shared,
        steps=steps,
        header=request.header,
        last_call=time.monotonic(),
    )

    def give_up() -> None:
        if shared:
            session.release()
        else:
            session.close()

    try:
        generation = session.publish(
            _stream_page(stream, request.timeout_seconds, more_coming=True),
            steps=build_step_fragments(_step_specs(steps), asset_base_url=session.asset_base_url),
            more_coming=True,
        )
        delivered = session.show_in_tab(generation, open_in_browser)
    except BaseException:
        give_up()
        raise
    if not delivered:
        give_up()
        return NOT_DELIVERED
    ask_id = secrets.token_hex(8)
    with _streams_lock:
        _streams[ask_id] = stream
    return {"ask_id": ask_id, "answers_so_far": []}


def _continue_stream(request: AskRequest) -> dict[str, Any]:
    ask_id = request.ask_id or ""
    with _streams_lock:
        stream = _streams.get(ask_id)
    if stream is None:
        raise InvalidAsk(
            f"no open ask with ask_id {ask_id!r}: it was answered or cancelled, or "
            f"expired after {_STREAM_IDLE_SECONDS / 60:.0f} minutes without a call"
        )
    stream.last_call = time.monotonic()

    submission = stream.session.submission()
    if submission is not None and submission.kind == "cancel":
        progress = stream.session.progress()
        _end_stream(ask_id)
        return _answer_from_submission(
            stream.steps, submission, multi=True, header=stream.header, progress=progress
        )

    added = list(request.questions or [])
    _check_question_ids(added, {step.id for step in stream.steps if step.id is not None})
    start = len(stream.steps)
    stream.steps.extend(added)
    stream.session.extend(
        _stream_page(stream, request.timeout_seconds, more_coming=request.more_coming),
        build_step_fragments(
            _step_specs(added), start, asset_base_url=stream.session.asset_base_url
        ),
        more_coming=request.more_coming,
    )
    if request.more_coming:
        return {"ask_id": ask_id, "answers_so_far": _answers_so_far(stream)}

    stream.waiting = True
    try:
        submission = stream.session.wait_for_answer(request.timeout_seconds)
        return _answer_from_submission(
            stream.steps,
            submission,
            multi=True,
            header=stream.header,
            progress=stream.session.progress(),
        )
    finally:
        _end_stream(ask_id)


def _run_ask(request: AskRequest) -> dict[str, Any]:
    """Open the page for `request` and block until it is answered, cancelled or idle.

    Returns `{"cancelled": True}` or `{"timed_out": True}` either way, or
    `{"not_delivered": True}` if the page never loaded in a browser at all. Otherwise, for a
    single question (the `question` shorthand), `{"selected", "free_text", "expanded"}`;
    for `questions`, `{"answers": [{"selected", "free_text", "expanded"}, ...]}`, one
    entry per question, in order, each with the question's `id` first if it set one.
    `expanded` is the option ids whose body drawer was opened at any point before the
    answer was sent, whether or not it was closed again afterwards. With
    SHARE_INTERACTION_SIGNALS on, every answer also has `changed_from` and each entry of
    `answers` also has `visits`.

    With `more_coming`, returns `{"ask_id", "answers_so_far"}` at once instead; see the
    module docstring for how the later calls of that streamed ask go.

    Tries to reuse the process's one shared browser tab first, falling back to a fresh
    one-off tab if that shared tab is already answering another call — see the module
    docstring.
    """
    request = request.validated()
    _expire_idle_streams()
    if request.resume is not None:
        request = _resumed_request(request).validated()
    if request.ask_id is not None:
        return _continue_stream(request)
    if request.more_coming:
        return _start_stream(request)
    steps = request.steps

    session = get_shared_session()
    if session.try_claim():
        try:
            return _run_ask_reusing_tab(request, steps, session)
        finally:
            session.release()
    return _run_ask_with_new_tab(request, steps)


_LONG_QUESTION_HINT = (
    "{where} {chars} chars in its first paragraph. Keep the question a short "
    "heading; put detail in lists, blockquotes or option `body`."
)


def _long_question_hint(steps: list[Question]) -> str | None:
    for index, step in enumerate(steps):
        first = first_paragraph(step.question)
        if len(first) > LONG_QUESTION_CHARS:
            where = f"Question {index + 1} has" if len(steps) > 1 else "The question has"
            return _LONG_QUESTION_HINT.format(where=where, chars=len(first))
    return None


def run_ask(request: AskRequest) -> dict[str, Any]:
    """`_run_ask`, plus a `hint` on the result if a question's first paragraph is long."""
    result = _run_ask(request)
    # A resume or a stream continuation carries no questions of its own to judge.
    if (
        request.resume is None
        and request.ask_id is None
        and (hint := _long_question_hint(request.steps))
    ):
        result["hint"] = hint
    return result

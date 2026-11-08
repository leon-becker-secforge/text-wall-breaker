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

A cancelled or abandoned ask that had answers entered returns a `resume` id: the pages
report every change as they go, the server keeps the last report as a draft, and a call
with `resume` reopens the same questions with those answers prefilled.

An interrupted ask (the user's "Stop here", see CONTEXT.md's Interruption) ends the call
at once with the answers entered so far, the stopped-at one marked, so the agent reads it
before anything else. On several questions it returns a `resume` id as well, keeping
every answer, the stopped-at one included.
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

from pydantic import BaseModel, Field, model_validator

from .glossary import load as load_glossary
from .listener import LoopbackListener, SessionListener, Submission
from .live_ask import ASK_ATTACH_INSTRUCTION, LiveAsk
from .live_common import LiveError
from .markdown import LONG_QUESTION_CHARS, first_paragraph
from .opener import BrowserUnavailable, find_opener
from .page import (
    ACCENT_ENV_VAR,
    InvalidAccent,
    build_multi_page,
    build_page,
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
# not treat it like an `abandoned` one. See listener.DELIVERY_SECONDS.
NOT_DELIVERED: dict[str, Any] = {
    "not_delivered": True,
    "reason": "the page never loaded in a browser within the delivery window",
}


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
        Field(description="Short plain text, shown as is (no Markdown)."),
    ]
    caveat: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional one-line precondition or cost that could flip the "
            "choice, visible even while the body is collapsed. Drawn as a highlighted "
            "strip, so use it sparingly: one on every option drowns out the one that "
            f"matters. Inline Markdown, at most {CAVEAT_MAX_CHARS} characters.",
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
            description="The option you lean towards: it gets a Recommended badge and "
            "its long body starts open, but stays unchecked for the user to pick. At "
            "most one per question unless multi_select. Put the reason in caveat or body.",
        ),
    ] = False


ALLOW_DEFER_DESCRIPTION = (
    'Offer a "Not yet" control, for a question that depends on something still open. '
    "A deferred answer comes back with deferred: true, no selection, and the reason "
    "(if any) in free_text. The deferred key only appears when this is set."
)


class Question(BaseModel):
    """One question on a page: the single-question shape, reused for `questions`."""

    id: Annotated[
        str | None,
        Field(
            description="Optional stable identifier, echoed as `id` on this question's "
            "entry in `answers` so a result is matched by id, not position. Unique "
            "within the ask. Never shown to the user."
        ),
    ] = None
    question: Annotated[str, Field(description="The question, as Markdown.")]
    options: Annotated[
        list[Option],
        Field(description="The choices to offer. May be omitted only if allow_free_text is true."),
    ] = []
    multi_select: Annotated[
        bool, Field(description="Allow more than one option to be selected.")
    ] = False
    allow_free_text: Annotated[
        bool, Field(description="Show a free-text box alongside the options.")
    ] = True
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
                "have no way to answer; supply options, or set allow_free_text"
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
    resume: str | None = None
    live: bool = False
    project: str | None = None
    free_text: str | None = None  # prefill of the single-question shorthand; set by resume only

    @model_validator(mode="after")
    def _fold_header_into_single_question(self) -> AskRequest:
        # The question keeps the lead: its first paragraph renders bold and is judged by
        # `_long_question_hint`, so the header follows as context.
        if self.resume is None and self.questions is None and self.header.strip():
            self.question = f"{self.question.rstrip()}\n\n{self.header.strip()}"
            self.header = ""
        return self

    def validated(self) -> AskRequest:
        """Return self, or raise `InvalidAsk` if the page would be unanswerable."""
        if self.resume is not None:
            if (
                self.question.strip()
                or self.options
                or self.questions is not None
                or self.header.strip()
            ):
                raise InvalidAsk("resume reopens an earlier ask and takes nothing else")
            return self
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
    live: str = "off",
) -> str:
    """The page for `request`, single-question or multi- as it calls for.

    `status_url`/`generation`/`live` are only set on the shared-session path — see
    `_run_ask_reusing_tab` — and default to "not reused, no discussion" for the one-shot
    path. `live` is "offer" (the operator may start a discussion) or "on" (one has started).
    """
    glossary = load_glossary(request.project)
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
            accent=_resolve_accent(),
            theme=read_saved_theme(),
            status_url=status_url,
            generation=generation,
            live=live,
            glossary=glossary,
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
        accent=_resolve_accent(),
        theme=read_saved_theme(),
        status_url=status_url,
        generation=generation,
        live=live,
        glossary=glossary,
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
        return AskRequest(questions=steps, header=draft.header)
    only = steps[0]
    return AskRequest(
        question=only.question,
        options=only.options,
        multi_select=only.multi_select,
        allow_free_text=only.allow_free_text,
        allow_defer=only.allow_defer,
        free_text=only.free_text,
    )


def _unanswered(
    result: dict[str, Any],
    steps: list[Question],
    header: str,
    *,
    multi: bool,
    progress: dict[str, Any] | None,
) -> dict[str, Any]:
    """`result` (cancelled or abandoned), plus a `resume` id if there is a draft to resume."""
    draft_id = _save_draft(steps, header, multi=multi, progress=progress)
    return {**result, "resume": draft_id} if draft_id else result


def _interrupted(
    steps: list[Question], data: dict[str, Any], *, multi: bool, header: str
) -> dict[str, Any]:
    """The answer to an ask the user stopped at one question.

    `answers` runs up to the stopped-at question or the last one answered after it,
    whichever is later: the agent matches unnamed questions by position, so entries in
    between stay, empty. The draft keeps all of it, so `resume` reopens the whole ask.
    """
    if not multi:
        return {**_extract_answer(steps[0], data), "interrupted": True}
    raw = data.get("answers")
    answers = _extract_answers(steps, raw)
    stop = data.get("interrupted")
    if not isinstance(stop, int) or isinstance(stop, bool) or not 0 <= stop < len(steps):
        stop = 0
    entered = [
        index
        for index, answer in enumerate(answers)
        if answer["selected"] or answer["free_text"] or answer.get("deferred")
    ]
    answers = answers[: max([stop, *entered]) + 1]
    answers[stop]["interrupted"] = True
    result: dict[str, Any] = {"answers": answers}
    draft_id = _save_draft(steps, header, multi=True, progress=data)
    if draft_id:
        result["resume"] = draft_id
    return result


def _answer_from_submission(
    steps: list[Question],
    submission: Submission | None,
    *,
    multi: bool,
    header: str = "",
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if submission is None:
        return _unanswered({"abandoned": True}, steps, header, multi=multi, progress=progress)
    if submission.kind == "cancel":
        return _unanswered({"cancelled": True}, steps, header, multi=multi, progress=progress)
    if submission.kind == "interrupt":
        return _interrupted(steps, submission.data, multi=multi, header=header)
    if not multi:
        return _extract_answer(steps[0], submission.data)
    return {"answers": _extract_answers(steps, submission.data.get("answers"))}


def _start_live(
    request: AskRequest, steps: list[Question], session: SessionListener, generation: int
) -> dict[str, Any]:
    """Turn the page `session` shows into a live session; the agent then calls `live`.

    The live session takes `session` over (see `session.detach_shared_session`), so the
    caller must not release it.
    """
    multi = request.questions is not None
    submit_url = f"http://127.0.0.1:{session.port}/submit?t={session.token}"
    status_url = f"http://127.0.0.1:{session.port}/status?t={session.token}"

    def prepare_refresh(
        question: str | None, options: list[Any] | None
    ) -> tuple[Callable[[int], str], list[tuple[str | None, set[str]]], list[str]]:
        if multi:
            raise LiveError("refresh needs an ask with one question, not `questions`")
        changes: dict[str, Any] = {}
        if question is not None:
            changes["question"] = question
        if options is not None:
            changes["options"] = [Option.model_validate(option) for option in options]
        new = steps[0].model_copy(update=changes)
        try:
            new.validated(label="refresh")
        except InvalidAsk as exc:
            raise LiveError(str(exc)) from exc
        ids = {option.id for option in new.options}
        picked = (session.progress() or {}).get("selected")
        dropped = [i for i in picked if i not in ids] if isinstance(picked, list) else []
        steps[0] = new
        refreshed = AskRequest(
            question=new.question,
            options=new.options,
            multi_select=new.multi_select,
            allow_free_text=new.allow_free_text,
            allow_defer=new.allow_defer,
        )
        return (
            lambda generation: _build_html(
                refreshed,
                [new],
                submit_url=submit_url,
                status_url=status_url,
                generation=generation,
                live="on",
            ),
            [(new.id, ids)],
            dropped,
        )

    live = LiveAsk(
        session,
        generation=generation,
        questions=[(step.id, {option.id for option in step.options}) for step in steps],
        build_result=lambda submission: _answer_from_submission(
            steps,
            submission,
            multi=multi,
            header=request.header,
            progress=session.progress(),
        ),
        prepare_refresh=prepare_refresh,
    )
    return {"live": {"session": live.id}, "instruction": ASK_ATTACH_INSTRUCTION}


def _run_ask_reusing_tab(
    request: AskRequest, steps: list[Question], session: SessionListener
) -> dict[str, Any]:
    """Answer `request` on `session`'s already-claimed, possibly already-open tab."""
    submit_url = f"http://127.0.0.1:{session.port}/submit?t={session.token}"
    status_url = f"http://127.0.0.1:{session.port}/status?t={session.token}"

    generation = session.publish(
        lambda generation: _build_html(
            request,
            steps,
            submit_url=submit_url,
            status_url=status_url,
            generation=generation,
            live="on" if request.live else "offer",
        )
    )
    # An already-open tab picks up the new content on its own (see page.py's
    # startReusePolling); show_in_tab only opens one if it doesn't.
    if not session.show_in_tab(generation, open_in_browser):
        return NOT_DELIVERED
    if request.live:
        return _start_live(request, steps, session, generation)

    started = False
    try:
        submission = session.wait_for_answer()
        started = submission is not None and submission.kind == "live_start"
    finally:
        # A started discussion keeps the page open for answers; see `_start_live`.
        if not started:
            session.end_question()
    if started:
        return _start_live(request, steps, session, generation)
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
            submission = listener.wait()
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


def _run_ask(request: AskRequest) -> dict[str, Any]:
    """Open the page for `request` and block until it is answered, cancelled or abandoned.

    Returns `{"cancelled": True}` or `{"abandoned": True}` (the tab was closed) either way, or
    `{"not_delivered": True}` if the page never loaded in a browser at all. Otherwise, for a
    single question (the `question` shorthand), `{"selected", "free_text", "expanded"}`;
    for `questions`, `{"answers": [{"selected", "free_text", "expanded"}, ...]}`, one
    entry per question, in order, each with the question's `id` first if it set one.
    `expanded` is the option ids whose body drawer was opened at any point before the
    answer was sent, whether or not it was closed again afterwards. With
    SHARE_INTERACTION_SIGNALS on, every answer also has `changed_from` and each entry of
    `answers` also has `visits`.

    An interrupted ask returns the answer or `answers` with `interrupted: True` on the
    stopped-at question, and `resume` when several questions were asked; see
    `_interrupted`.

    Tries to reuse the process's one shared browser tab first, falling back to a fresh
    one-off tab if that shared tab is already answering another call — see the module
    docstring.
    """
    request = request.validated()
    if request.resume is not None:
        request = _resumed_request(request).validated()
    steps = request.steps

    session = get_shared_session()
    if session.try_claim():
        result: dict[str, Any] = {}
        try:
            result = _run_ask_reusing_tab(request, steps, session)
            return result
        finally:
            # A live session keeps the tab and its claim until it ends.
            if "live" not in result:
                session.release()
    if request.live:
        raise InvalidAsk(
            "live=true needs the shared browser tab, which another call is using; "
            "try again when it is free"
        )
    return _run_ask_with_new_tab(request, steps)


_LONG_QUESTION_HINT = (
    "{where} {chars} chars in its first paragraph. Open with a short lead "
    "sentence; put the context in further paragraphs, lists or blockquotes."
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
    # A copy: NOT_DELIVERED is a shared constant, and `hint` must never stick to it.
    result = dict(_run_ask(request))
    # A resume carries no questions of its own to judge, and a page nobody saw says
    # nothing about its wording.
    if (
        request.resume is None
        and not result.get("not_delivered")
        and (hint := _long_question_hint(request.steps))
    ):
        result["hint"] = hint
    return result

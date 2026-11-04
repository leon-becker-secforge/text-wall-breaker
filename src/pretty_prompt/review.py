"""One `review` round trip: validate, write the page, open it, wait for the submission.

`run_review` is synchronous and blocking, exactly the same shape as `ask.run_ask` — see
there for the shared-tab/one-off-tab split this mirrors, and the module docstring for why
`review` is its own tool rather than a mode of `ask` (ADR 0001).

The one deliberate divergence from `ask`: cancelling a review returns whatever comments
and overall_note the operator already left, instead of discarding them the way `ask`'s
all-or-nothing cancel does — see ADR 0002. Submit and Cancel share one payload shape;
only `Submission.kind` tells them apart.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .ask import BrowserUnavailable, open_in_browser
from .diff import DiffLine, InvalidDiff, parse_unified_diff
from .listener import LoopbackListener, SessionListener, Submission
from .page import (
    ACCENT_ENV_VAR,
    InvalidAccent,
    build_review_page,
    parse_accent,
    read_saved_theme,
)
from .session import get_shared_session

__all__ = ["BrowserUnavailable", "InvalidReview", "Page", "ReviewRequest", "run_review"]

# The page never loaded, so nobody saw it — same reasoning as ask.NOT_DELIVERED.
NOT_DELIVERED: dict[str, Any] = {"not_delivered": True}


class InvalidReview(ValueError):
    """The request cannot produce a reviewable page."""


class Page(BaseModel):
    """One file's diff, shown as one page."""

    path: Annotated[
        str, Field(description="The file's path, shown on the page and echoed on comments.")
    ]
    diff: Annotated[
        str,
        Field(
            description="Raw unified diff text for this file, as `git diff` prints it, "
            "with at least one `@@` hunk header."
        ),
    ]
    blurb: Annotated[
        str | None,
        Field(description="Optional Markdown at the top of this page only, e.g. why it changed."),
    ] = None

    def validated(self, *, label: str) -> Page:
        if not self.path.strip():
            raise InvalidReview(f"{label}: path is empty")
        if not self.diff.strip():
            raise InvalidReview(f"{label}: diff is empty")
        try:
            parse_unified_diff(self.diff)
        except InvalidDiff as exc:
            raise InvalidReview(f"{label}: {exc}") from exc
        return self


class ReviewRequest(BaseModel):
    """A validated `review` call — see CONTEXT.md's "Review" section."""

    pages: list[Page] = []
    header: str | None = None
    title: str = "Claude Code"

    def validated(self) -> ReviewRequest:
        if not self.pages:
            raise InvalidReview("pages is empty; give pages, or repo to diff a git repository")
        for index, page in enumerate(self.pages):
            page.validated(label=f"pages[{index}]")
        return self


def _resolve_accent() -> str | None:
    """Same reasoning as `ask._resolve_accent` — see there."""
    value = os.environ.get(ACCENT_ENV_VAR)
    if not value:
        return None
    try:
        parse_accent(value)
    except InvalidAccent:
        return None
    return value


def _build_html(
    request: ReviewRequest,
    *,
    submit_url: str,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    return build_review_page(
        pages=[
            {"path": page.path, "diff": page.diff, "blurb": page.blurb} for page in request.pages
        ],
        header=request.header,
        title=request.title,
        accent=_resolve_accent(),
        theme=read_saved_theme(),
        submit_url=submit_url,
        status_url=status_url,
        generation=generation,
    )


_MARKERS = {"add": "+", "del": "-", "ctx": " "}


def _addressable_rows(pages: list[Page]) -> dict[str, list[DiffLine]]:
    """Per path, the rows of its diff a comment can address, in display order.

    A comment's coordinates are checked against these rather than trusted outright — the
    page is the only thing that should ever POST here, but the result is what the agent
    acts on, same reasoning as `ask._extract_answer` filtering ids against known options.
    The rows' text doubles as `context` in the returned comment, so the agent doesn't have
    to re-parse its own diff to see what a comment was about.
    """
    return {
        page.path: [
            row
            for row in parse_unified_diff(page.diff)
            if row.side is not None and row.line is not None
        ]
        for page in pages
    }


def _is_line(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _context(rows: list[DiffLine]) -> str:
    """One row's text as is; a range's rows one per line, each with its diff marker.

    A range may mix deleted and added rows, which look alike without the marker.
    """
    if len(rows) == 1:
        return rows[0].text
    return "\n".join(_MARKERS[row.kind] + row.text for row in rows)


def _valid_quote(raw: object, rows: list[DiffLine]) -> str | None:
    """`raw` if it is real text of `rows` and shorter than all of it, else None.

    Whitespace is compared loosely: the page reads a selection back from rendered text.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    haystack = " ".join("\n".join(row.text for row in rows).split())
    needle = " ".join(raw.split())
    return raw.strip() if needle in haystack and needle != haystack else None


def _extract_comments(pages: list[Page], raw: object) -> list[dict[str, Any]]:
    rows_by_path = _addressable_rows(pages)
    index_by_path = {
        path: {(row.side, row.line): i for i, row in enumerate(rows)}
        for path, rows in rows_by_path.items()
    }
    comments: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return comments
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        side = entry.get("side")
        line = entry.get("line")
        text = entry.get("text")
        if not isinstance(path, str) or side not in ("old", "new") or not _is_line(line):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        index = index_by_path.get(path, {})
        start = index.get((side, line))
        if start is None:
            continue
        end = start
        end_side, end_line = entry.get("end_side"), entry.get("end_line")
        if end_side is not None or end_line is not None:
            if end_side not in ("old", "new") or not _is_line(end_line):
                continue
            found = index.get((end_side, end_line))
            if found is None or found < start:
                continue
            end = found
        rows = rows_by_path[path][start : end + 1]
        comment: dict[str, Any] = {"path": path, "side": side, "line": line}
        if end != start:
            comment["end_side"] = end_side
            comment["end_line"] = end_line
        comment["text"] = text.strip()
        comment["context"] = _context(rows)
        quote = _valid_quote(entry.get("quote"), rows)
        if quote is not None:
            comment["quote"] = quote
        comments.append(comment)
    return comments


def _extract_page_notes(pages: list[Page], raw: object) -> list[dict[str, str]]:
    """The non-empty page notes whose path is one of `pages`, in the order sent."""
    if not isinstance(raw, list):
        return []
    paths = {page.path for page in pages}
    notes: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path, text = entry.get("path"), entry.get("text")
        if isinstance(path, str) and path in paths and isinstance(text, str) and text.strip():
            notes.append({"path": path, "text": text.strip()})
    return notes


def _result_from_submission(
    pages: list[Page], submission: Submission | None, progress: dict[str, Any] | None
) -> dict[str, Any]:
    """Submit, cancel or abandon, all in one shape.

    An abandoned review (no submission) returns what the page last reported in its
    heartbeat, the same way cancel keeps its comments (ADR 0002).
    """
    data = submission.data if submission is not None else (progress or {})
    comments = _extract_comments(pages, data.get("comments"))
    note = data.get("overall_note")
    result: dict[str, Any] = {
        "comments": comments,
        "page_notes": _extract_page_notes(pages, data.get("page_notes")),
        "overall_note": note.strip() if isinstance(note, str) and note.strip() else None,
    }
    if submission is None:
        result["abandoned"] = True
    elif submission.kind == "cancel":
        result["cancelled"] = True
    return result


def _run_review_reusing_tab(request: ReviewRequest, session: SessionListener) -> dict[str, Any]:
    """Review `request` on `session`'s already-claimed, possibly already-open tab."""
    submit_url = f"http://127.0.0.1:{session.port}/submit?t={session.token}"
    status_url = f"http://127.0.0.1:{session.port}/status?t={session.token}"

    generation = session.publish(
        lambda generation: _build_html(
            request, submit_url=submit_url, status_url=status_url, generation=generation
        )
    )
    if not session.show_in_tab(generation, open_in_browser):
        return NOT_DELIVERED

    try:
        submission = session.wait_for_answer()
    finally:
        session.end_question()
    return _result_from_submission(request.pages, submission, session.progress())


def _run_review_with_new_tab(request: ReviewRequest) -> dict[str, Any]:
    """Review `request` with its own one-off tab — used when the shared one is busy."""
    token = secrets.token_hex(16)

    with LoopbackListener(token) as listener:
        submit_url = f"http://127.0.0.1:{listener.port}/submit?t={token}"
        html = _build_html(request, submit_url=submit_url)

        directory = Path(tempfile.mkdtemp(prefix="pretty-prompt-review-"))
        try:
            page = directory / "review.html"
            page.write_text(html, encoding="utf-8")
            open_in_browser(page)
            submission = listener.wait()
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if not listener.page_seen:
            return NOT_DELIVERED
        progress = listener.progress()

    return _result_from_submission(request.pages, submission, progress)


def run_review(request: ReviewRequest) -> dict[str, Any]:
    """Open the page for `request` and block until it is submitted, cancelled, or abandoned.

    Returns `{"comments": [...], "page_notes": [{"path", "text"}], "overall_note": "..." |
    None}` on Submit, the same
    shape plus `"cancelled": True` on Cancel (comments/overall_note reflect whatever
    existed at that point — see the module docstring and ADR 0002), the same plus
    `"abandoned": True` if the tab was closed without either (comments/overall_note from
    the page's last progress report), or `{"not_delivered": True}`
    if the page never loaded in a browser at all. Each comment is
    `{"path", "side", "line", "text", "context"}`, `side` being "old" or "new", `line`
    that side's 1-based line number in the file at `path`, and `context` the diff line's
    own text (not the operator's remark) — see CONTEXT.md's "Comment".

    Tries to reuse the process's one shared browser tab first (the same one `ask`/`show`
    use), falling back to a fresh one-off tab if that shared tab is already busy with
    another call — see `ask.run_ask`'s module docstring for the same split.
    """
    request = request.validated()

    session = get_shared_session()
    if session.try_claim():
        try:
            return _run_review_reusing_tab(request, session)
        finally:
            session.release()
    return _run_review_with_new_tab(request)

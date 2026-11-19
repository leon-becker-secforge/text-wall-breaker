"""A live review session: one review page the operator and the agent work through together.

See ADR 0004 and CONTEXT.md's "Live review". The page talks to this through the shared
`SessionListener` (`POST /submit` with kind "live", `GET /live`); the agent through
`live` calls, each running `LiveSession.call` in a worker thread. The session runs on the
listener it took over from the shared one (see `session.detach_shared_session`) and closes
it when it ends.

Comments live in the page and reach the server in its progress reports, each tagged with
the generation of the page it was made on. A refresh publishes a new generation; a report
from an older one is re-anchored onto the current diff before it is kept, so a comment the
operator saved just before the page reloaded is not lost.
"""

from __future__ import annotations

import difflib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .diff import DiffLine, parse_unified_diff
from .listener import ABANDON_SECONDS, Submission
from .live_common import POLL_SECONDS, LiveCall, LiveError, Reply, Tab, no_such_session
from .markdown import render_markdown
from .review import (
    InvalidReview,
    Note,
    Page,
    ReviewRequest,
    _addressable_rows,
    _extract_comments,
    _result_from_submission,
)
from .session import detach_shared_session, forget_live, register_live

__all__ = ["LiveError", "LiveSession", "Reply", "RepoSource"]

ATTACH_INSTRUCTION = (
    "Live review: call live(session) now and keep calling it until it returns the "
    "report. Each call returns the operator's new messages; answer with replies, change "
    "code and pass refresh=true. Use wait=false to acknowledge before long work."
)

_POLL_SECONDS = POLL_SECONDS

# Older generations kept for re-anchoring a late progress report. A page reloads within
# seconds of a refresh, so only the last few can still report.
_KEEP_GENERATIONS = 4


@dataclass(frozen=True)
class RepoSource:
    """The `repo` arguments a session was opened with, re-run by a refresh."""

    repo: str
    git_range: str | None
    paths: list[str] | None
    context_lines: int


def _new_side(page: Page, files: dict[str, str]) -> list[str]:
    text = files.get(page.path)
    if text is not None:
        return text.splitlines()
    return [row.text for row in parse_unified_diff(page.diff) if row.new_line is not None]


def _interdiff(before: list[str], after: list[str]) -> list[dict[str, Any]]:
    """Rows of the change from `before` to `after`, for the page's "since you looked" view."""
    text = "\n".join(difflib.unified_diff(before, after, lineterm="", n=3))
    if "@@" not in text:
        return []
    rows: list[dict[str, Any]] = []
    for row in parse_unified_diff(text):
        rows.append({"kind": row.kind, "text": row.text, "old": row.old_line, "new": row.new_line})
    return rows


def _run(rows: list[DiffLine], first: int, last: int) -> list[tuple[str | None, str]]:
    return [(row.side, row.text) for row in rows[first : last + 1]]


def _find_run(rows: list[DiffLine], run: list[tuple[str | None, str]], near: int) -> int | None:
    """Start index in `rows` of the run of rows matching `run`, nearest to line `near`."""
    best: int | None = None
    for i in range(len(rows) - len(run) + 1):
        if _run(rows, i, i + len(run) - 1) != run:
            continue
        distance = abs((rows[i].line or 0) - near)
        if best is None or distance < abs((rows[best].line or 0) - near):
            best = i
    return best


def _nearest(rows: list[DiffLine], side: str, line: int) -> dict[str, Any] | None:
    same = [row for row in rows if row.side == side] or rows
    if not same:
        return None
    row = min(same, key=lambda r: abs((r.line or 0) - line))
    return {"side": row.side, "line": row.line}


class LiveSession:
    """State of one live session, shared by the page's requests and the agent's calls."""

    def __init__(
        self,
        session: Tab,
        request: ReviewRequest,
        source: RepoSource | None,
        *,
        build_html: Callable[[ReviewRequest, int], str],
        load_repo: Callable[[RepoSource], tuple[list[Page], dict[str, str]]],
        generation: int,
    ) -> None:
        self.id = "ls-" + secrets.token_hex(4)
        self._session = session
        self._source = source
        self._build_html = build_html
        self._load_repo = load_repo
        self._cond = threading.Condition()
        self._generation = generation
        self._requests: dict[int, ReviewRequest] = {generation: request}
        self._request = request
        self._comments: list[dict[str, Any]] = []
        self._outdated: list[dict[str, Any]] = []
        self._page_notes: Any = []
        self._overall_note: Any = ""
        self._threads: dict[str, dict[str, Any]] = {}
        self._chat: list[dict[str, str]] = []
        self._inbox: list[dict[str, Any]] = []
        self._version = 0
        # `_blocking`: a call waits for the operator (shown on the page). `_busy`: a call
        # is anywhere past its first check, refresh included.
        self._blocking = False
        self._busy = False
        self._since = time.monotonic()
        self._final: Submission | None = None
        self._abandoned = False
        self._done = False
        self._closed = False
        self._initial = {p.path: _new_side(p, request.files) for p in request.pages}
        self._seen: dict[str, list[str]] = {}
        self._interdiffs: dict[str, list[dict[str, Any]]] = {}
        session.live = self
        detach_shared_session(session)
        register_live(self.id, self)

    # ---- the page ----

    def poll(self, *, restore: bool) -> dict[str, Any]:
        with self._cond:
            body: dict[str, Any] = {
                "session": self.id,
                "generation": self._generation,
                "version": self._version,
                "agent": {
                    "waiting": self._blocking,
                    "seconds": int(time.monotonic() - self._since),
                },
                "threads": list(self._threads.values()),
                "chat": self._chat,
                "ended": self._final is not None or self._abandoned,
            }
            if restore:
                body["restore"] = {
                    "comments": self._comments,
                    "outdated": self._outdated,
                    "page_notes": self._page_notes,
                    "overall_note": self._overall_note,
                    "interdiffs": self._interdiffs,
                }
            return body

    def handle(self, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """One operator event from the page: a thread, a reply, a resolve, chat, a view."""
        kind = data.get("type")
        with self._cond:
            if self._final is not None or self._done:
                return 409, {"error": "the session has ended"}
            if kind == "thread":
                return self._start_thread(data.get("comment"))
            if kind == "seen":
                path = data.get("path")
                page = next((p for p in self._request.pages if p.path == path), None)
                if page is not None:
                    self._seen[page.path] = _new_side(page, self._request.files)
                return 200, {}
            if kind == "drop":
                key = (data.get("path"), data.get("text"), data.get("context"))
                self._outdated = [
                    o for o in self._outdated if (o["path"], o["text"], o["context"]) != key
                ]
                return 200, {}
            text = data.get("text")
            if kind == "chat":
                if not isinstance(text, str) or not text.strip():
                    return 400, {"error": "empty message"}
                self._chat.append({"from": "operator", "text": text.strip()})
                self._push({"chat": text.strip()})
                return 200, {}
            thread = self._threads.get(str(data.get("thread")))
            if thread is None:
                return 404, {"error": "no such thread"}
            if kind == "reply":
                if not isinstance(text, str) or not text.strip():
                    return 400, {"error": "empty message"}
                thread["messages"].append({"from": "operator", "text": text.strip()})
                thread["done"] = False
                self._push({"thread": thread["id"], "text": text.strip()})
                return 200, {}
            if kind in ("resolve", "reopen"):
                thread["resolved"] = kind == "resolve"
                self._push({"resolved" if kind == "resolve" else "reopened": thread["id"]})
                return 200, {}
        return 400, {"error": "unknown event"}

    def _start_thread(self, raw: object) -> tuple[int, dict[str, Any]]:
        found = _extract_comments(self._request.pages, [raw], self._request.expansion())
        if not found:
            return 400, {"error": "not a comment on this diff"}
        comment = found[0]
        thread_id = f"t{len(self._threads) + 1}"
        anchor = {k: v for k, v in comment.items() if k != "text"}
        self._threads[thread_id] = {
            "id": thread_id,
            "anchor": anchor,
            "messages": [{"from": "operator", "text": comment["text"]}],
            "resolved": False,
            "done": False,
            "outdated": False,
        }
        self._push({"thread": thread_id, **comment})
        return 200, {"id": thread_id}

    def _push(self, message: dict[str, Any]) -> None:
        self._inbox.append(message)
        self._version += 1
        self._cond.notify_all()

    def on_progress(self, data: dict[str, Any]) -> None:
        with self._cond:
            self._take_progress(data)

    def on_submission(self, submission: Submission) -> None:
        if submission.kind not in ("submit", "cancel"):
            return
        with self._cond:
            self._take_progress(submission.data)
            self._final = submission
            self._version += 1
            self._cond.notify_all()

    def _take_progress(self, data: dict[str, Any]) -> None:
        generation = data.get("generation", self._generation)
        comments = data.get("comments")
        if isinstance(comments, list):
            request = self._requests.get(generation) if isinstance(generation, int) else None
            if request is not None:
                self._comments = self._reanchor(comments, request, self._request)
        if "page_notes" in data:
            self._page_notes = data.get("page_notes")
        if "overall_note" in data:
            self._overall_note = data.get("overall_note")

    def _reanchor(
        self, raw: list[Any], old: ReviewRequest, new: ReviewRequest
    ) -> list[dict[str, Any]]:
        """`raw` page comments on `old`'s diff, moved onto `new`'s.

        A comment whose rows still exist (same text and side, contiguous) moves to the
        occurrence nearest its old line; any other one goes to `_outdated`.
        """
        old_rows = _addressable_rows(old.pages, old.expansion())
        new_rows = _addressable_rows(new.pages, new.expansion())
        kept: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            found = _extract_comments(old.pages, [entry], old.expansion())
            if not found:
                continue
            comment = found[0]
            thread = entry.get("thread")
            if isinstance(thread, str) and thread in self._threads:
                comment["thread"] = thread
            if old is new:
                kept.append(comment)
                continue
            rows = old_rows[comment["path"]]
            index = {(r.side, r.line): i for i, r in enumerate(rows)}
            first = index[(comment["side"], comment["line"])]
            last = index.get((comment.get("end_side"), comment.get("end_line")), first)
            run = _run(rows, first, last)
            targets = new_rows.get(comment["path"], [])
            start = _find_run(targets, run, comment["line"]) if targets else None
            if start is None:
                self._mark_outdated(comment, targets)
                continue
            moved = dict(comment)
            moved["side"], moved["line"] = targets[start].side, targets[start].line
            if last != first:
                end = targets[start + last - first]
                moved["end_side"], moved["end_line"] = end.side, end.line
            kept.append(moved)
            if "thread" in moved:
                self._threads[moved["thread"]]["anchor"].update(
                    {k: moved[k] for k in ("side", "line", "end_side", "end_line") if k in moved}
                )
        return kept

    def _mark_outdated(self, comment: dict[str, Any], rows: list[DiffLine]) -> None:
        key = (comment.get("thread"), comment["path"], comment["text"], comment["context"])
        for existing in self._outdated:
            if (
                existing.get("thread"),
                existing["path"],
                existing["text"],
                existing["context"],
            ) == key:
                return
        entry = {
            **comment,
            "outdated": True,
            "near": _nearest(rows, comment["side"], comment["line"]),
        }
        self._outdated.append(entry)
        if "thread" in comment:
            self._threads[comment["thread"]]["outdated"] = True

    # ---- the agent ----

    def call(self, request: LiveCall) -> dict[str, Any]:
        """One `live` call: post, maybe refresh, then wait for the operator."""
        try:
            return self._call(request)
        finally:
            # Outside `_cond`: closing waits for the listener, whose handlers take it.
            with self._cond:
                close, self._closed = self._done and not self._closed, self._closed or self._done
            if close:
                self._session.close()

    def _call(self, request: LiveCall) -> dict[str, Any]:
        if request.has_ask_arguments():
            raise LiveError("question and options are for ask sessions")
        pages: list[Page] | None = request.pages
        notes: list[Note] | None = request.notes
        wants_refresh = (
            request.refresh
            or bool(pages)
            or notes is not None
            or request.git_range is not None
            or request.paths is not None
        )
        with self._cond:
            if request.session_id != self.id or self._done:
                raise no_such_session(request.session_id)
            # `_busy` from here on, not only while waiting: a refresh runs outside the
            # lock, and a second call slipping in then would refresh from the same state.
            if self._busy:
                raise LiveError("another live call is already waiting on this session")
            if self._final is not None or self._abandoned:
                return self._finish(refresh_dropped=wants_refresh)
            for reply in request.replies:
                self._check_reply(reply)
            for reply in request.replies:
                self._post(reply)
            self._busy = True
            self._since = time.monotonic()
            self._version += 1
        try:
            if wants_refresh:
                self._refresh(
                    git_range=request.git_range, paths=request.paths, pages=pages, notes=notes
                )
            return self._wait(request)
        finally:
            with self._cond:
                self._busy = False

    def _wait(self, request: LiveCall) -> dict[str, Any]:
        with self._cond:
            if not request.wait:
                return {"posted": True, "messages": self._take_inbox()}
            self._blocking = True
            self._since = time.monotonic()
            self._version += 1
            try:
                while True:
                    # Before anything is taken: the caller is gone, so a message or the
                    # result taken now would be lost. The next call gets them.
                    if request.cancel is not None and request.cancel.is_set():
                        raise LiveError("the call was cancelled")
                    if self._final is not None:
                        return self._finish(refresh_dropped=False)
                    if self._inbox:
                        return {"messages": self._take_inbox()}
                    if self._session.idle_seconds() > ABANDON_SECONDS:
                        self._abandoned = True
                        return self._finish(refresh_dropped=False)
                    self._cond.wait(_POLL_SECONDS)
            finally:
                self._blocking = False
                self._since = time.monotonic()
                self._version += 1

    def _check_reply(self, reply: Reply) -> None:
        if not reply.text.strip():
            raise LiveError("a reply needs text")
        if reply.to != "chat" and reply.to not in self._threads:
            raise LiveError(f'no thread {reply.to!r}; reply to a thread id or to "chat"')

    def _post(self, reply: Reply) -> None:
        text = reply.text.strip()
        entry = {"from": "agent", "text": text, "html": render_markdown(text)}
        if reply.to == "chat":
            self._chat.append(entry)
            return
        thread = self._threads[reply.to]
        thread["messages"].append(entry)
        thread["done"] = reply.done

    def _take_inbox(self) -> list[dict[str, Any]]:
        messages, self._inbox = self._inbox, []
        return messages

    def _refresh(
        self,
        *,
        git_range: str | None,
        paths: list[str] | None,
        pages: list[Page] | None,
        notes: list[Note] | None,
    ) -> None:
        old = self._request
        if self._source is not None and not pages:
            source = RepoSource(
                repo=self._source.repo,
                git_range=git_range if git_range is not None else self._source.git_range,
                paths=paths if paths is not None else self._source.paths,
                context_lines=self._source.context_lines,
            )
            new_pages, files = self._load_repo(source)
            self._source = source
        else:
            if self._source is None and (git_range is not None or paths is not None):
                raise LiveError("git_range and paths need a session opened with repo")
            by_path = {p.path: p for p in old.pages}
            for page in pages or []:
                by_path[page.path] = page
            new_pages, files = list(by_path.values()), dict(old.files)
            for page in pages or []:
                files.pop(page.path, None)
        request = ReviewRequest(
            pages=new_pages,
            notes=self._carry_notes(old, new_pages, files, notes),
            header=old.header,
            title=old.title,
            files=files,
            root=old.root,
        )
        try:
            request.validated()
        except InvalidReview as exc:
            raise LiveError(f"refresh: {exc}") from exc
        with self._cond:
            if self._final is not None:
                return
            self._comments = self._reanchor(self._comments_raw(), old, request)
            interdiffs: dict[str, list[dict[str, Any]]] = {}
            for page in request.pages:
                before = self._seen.get(page.path, self._initial.get(page.path))
                after = _new_side(page, request.files)
                if before is not None and before != after:
                    rows = _interdiff(before, after)
                    if rows:
                        interdiffs[page.path] = rows
            self._interdiffs = interdiffs
            self._request = request
            generation = self._session.publish(lambda g: self._build_html(request, g))
            self._generation = generation
            self._requests[generation] = request
            for g in sorted(self._requests)[:-_KEEP_GENERATIONS]:
                del self._requests[g]
            self._version += 1

    def _comments_raw(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._comments]

    def _carry_notes(
        self,
        old: ReviewRequest,
        pages: list[Page],
        files: dict[str, str],
        notes: list[Note] | None,
    ) -> list[Note]:
        replaced = {n.path for n in notes or []}
        carried: list[Note] = [n for n in old.notes if n.path not in replaced]
        kept: list[Note] = []
        for note in carried:
            probe = ReviewRequest(pages=pages, notes=[note], files=files)
            try:
                probe.page_notes()
            except InvalidReview:
                continue
            kept.append(note)
        return kept + list(notes or [])

    def _finish(self, *, refresh_dropped: bool) -> dict[str, Any]:
        request = self._request
        progress = {
            "comments": self._comments,
            "page_notes": self._page_notes,
            "overall_note": self._overall_note,
        }
        data = Submission(kind=self._final.kind, data=progress) if self._final else None
        result = _result_from_submission(request.pages, data, progress, request.expansion())
        result["comments"] += [
            {k: v for k, v in c.items() if k not in ("near", "thread")} for c in self._outdated
        ]
        result["threads"] = [self._thread_summary(t) for t in self._threads.values()]
        if refresh_dropped:
            result["refresh_dropped"] = True
        self._done = True
        self._session.live = None
        self._session.end_question()
        forget_live(self.id)
        return result

    @staticmethod
    def _thread_summary(thread: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {"id": thread["id"], **thread["anchor"]}
        summary.pop("context", None)
        summary.pop("quote", None)
        summary["resolved"] = thread["resolved"]
        if thread["outdated"]:
            summary["outdated"] = True
        last = thread["messages"][-1]
        summary["last"] = {"from": last["from"], "text": last["text"]}
        return summary

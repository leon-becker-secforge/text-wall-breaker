"""One `show` call: build the page, open it, and — unless told to wait — return at once.

Two ways a call gets its tab, exactly mirroring `ask.run_ask`:

  1. Reuse the process's one shared `SessionListener` tab (`session.get_shared_session`),
     if nothing else is using it right now. This is the same session `ask` claims, so an
     `ask` and a `show` in the same process land in one tab between them, not two.
  2. Fall back to a one-off tab if the shared one is already claimed by an overlapping
     call, or still holds an unacknowledged report (see `SessionListener.try_claim`).

Unlike `ask`, most `show` calls don't wait for anything: `wait_for_acknowledge=False`
(the default) publishes the page and returns immediately once the browser has it. Only a
call that opts into `wait_for_acknowledge` blocks on a response, and only the Acknowledge
button ever sends one — Save is pure client-side Blob download and never reaches here.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .ask import BrowserUnavailable, not_delivered, open_in_browser
from .listener import LoopbackListener, SessionListener, Submission
from .page import (
    build_show_page,
    read_saved_theme,
    resolve_accent,
)
from .session import get_shared_session

__all__ = ["BrowserUnavailable", "InvalidShow", "ShowRequest", "run_show", "show_status"]


class _OwnTab:
    """Title of the last report shown in a one-off tab, which `show_status` cannot track."""

    value: str | None = None


_own_tab_title = _OwnTab()


class InvalidShow(ValueError):
    """The request has nothing to show, or can't produce a page worth opening."""


class ShowRequest(BaseModel):
    """A validated `show` call.

    Either `content` (one Markdown document) or `slides` (several, shown one at a time
    as a deck) is set — never both. See `page.build_show_page`/`_render_deck` for how
    `slides` renders; `header`/`footer` only mean anything alongside `slides`.
    """

    content: str = ""
    slides: list[str] | None = None
    header: str | None = None
    footer: str | None = None
    title: str = "Claude Code"
    save_button: bool = True
    acknowledge_button: bool = True
    wait_for_acknowledge: bool = False

    def validated(self) -> ShowRequest:
        if self.wait_for_acknowledge and not self.acknowledge_button:
            raise InvalidShow("wait_for_acknowledge requires acknowledge_button")
        if self.slides is not None:
            if self.content.strip():
                raise InvalidShow("set either content or slides, not both")
            if not self.slides:
                raise InvalidShow("slides is empty")
            if not any(slide.strip() for slide in self.slides):
                raise InvalidShow("slides has no content")
            return self
        if self.header is not None or self.footer is not None:
            raise InvalidShow("header/footer require slides")
        if not self.content.strip():
            raise InvalidShow("content is empty")
        return self


def _build_html(
    request: ShowRequest,
    *,
    submit_url: str | None,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    return build_show_page(
        content=request.content,
        slides=request.slides,
        header=request.header,
        footer=request.footer,
        title=request.title,
        accent=resolve_accent(),
        theme=read_saved_theme(),
        save_button=request.save_button,
        acknowledge_button=request.acknowledge_button,
        submit_url=submit_url,
        status_url=status_url,
        generation=generation,
    )


def _result_from_submission(submission: Submission | None) -> dict[str, Any]:
    if submission is None:
        return {"abandoned": True}
    return {"acknowledged": submission.kind == "acknowledge"}


def _run_show_reusing_tab(request: ShowRequest, session: SessionListener) -> dict[str, Any]:
    """Show `request` on `session`'s already-claimed, possibly already-open tab.

    The shared session is always running once any call has touched it, so a submit URL
    is always available here regardless of `wait_for_acknowledge` — an Acknowledge click
    on a call that isn't waiting still reaches the session, it's just that nothing reads
    it back out (the next `publish()` discards it, same as an ignored probe).
    """
    submit_url, status_url = session.submit_url, session.status_url

    _own_tab_title.value = None
    generation = session.publish(
        lambda generation: _build_html(
            request, submit_url=submit_url, status_url=status_url, generation=generation
        )
    )
    if request.acknowledge_button:
        # After publish, which clears any earlier report. The page isn't served until
        # show_in_tab opens or reloads the tab, so no click can race this.
        session.mark_report(request.title)
    if not session.show_in_tab(generation, open_in_browser):
        return not_delivered()
    if not request.wait_for_acknowledge:
        return {"opened": True}
    submission = session.wait_for_answer()
    return _result_from_submission(submission)


_REPORT_PREFIX = "text-wall-breaker-show-"
# A report shown without waiting outlives its call: the tab still reads the file. A day
# later nobody does, so the next one-off report removes it.
_REPORT_MAX_AGE_SECONDS = 24 * 3600


def _sweep_old_reports() -> None:
    """Remove this user's one-off report directories older than a day."""
    cutoff = time.time() - _REPORT_MAX_AGE_SECONDS
    uid = os.getuid() if hasattr(os, "getuid") else None
    try:
        entries = list(os.scandir(tempfile.gettempdir()))
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(_REPORT_PREFIX):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        # Only real directories of this user: never follow a planted symlink.
        if not entry.is_dir(follow_symlinks=False) or info.st_mtime > cutoff:
            continue
        if uid is not None and info.st_uid != uid:
            continue
        shutil.rmtree(entry.path, ignore_errors=True)


def _run_show_with_new_tab(request: ShowRequest) -> dict[str, Any]:
    """Show `request` with its own one-off tab, used when the shared one is busy.

    Only binds a listener when `wait_for_acknowledge` is set — otherwise there is nothing
    to wait for, and a socket kept alive with no one polling it would outlive this call
    for no reason. Without a listener, Acknowledge still shows on the page but is purely
    a local "seen" mark (see build_show_page's docstring); Save is unaffected either way.
    """
    _sweep_old_reports()
    directory = Path(tempfile.mkdtemp(prefix=_REPORT_PREFIX))
    page = directory / "show.html"

    if not request.wait_for_acknowledge:
        html = _build_html(request, submit_url=None)
        page.write_text(html, encoding="utf-8")
        open_in_browser(page)
        _own_tab_title.value = request.title if request.acknowledge_button else None
        return {"opened": True, "path": str(page)}

    token = secrets.token_hex(16)
    with LoopbackListener(token) as listener:
        submit_url = listener.submit_url
        html = _build_html(request, submit_url=submit_url)
        page.write_text(html, encoding="utf-8")
        try:
            open_in_browser(page)
            submission = listener.wait()
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if not listener.page_seen:
            return not_delivered()
    return _result_from_submission(submission)


def run_show(request: ShowRequest) -> dict[str, Any]:
    """Open the page for `request`. Returns `{"opened": True[, "path": ...]}` unless
    `wait_for_acknowledge` is set, in which case it blocks until the Acknowledge button
    is clicked (`{"acknowledged": True}`) or the tab is closed (`{"abandoned": True}`).

    Tries the process's one shared browser tab first — the same one `ask` uses — falling
    back to a fresh one-off tab if that shared tab is already asking something else or
    still holds an unacknowledged report. See the module docstring.
    """
    request = request.validated()

    session = get_shared_session()
    if session.try_claim():
        try:
            return _run_show_reusing_tab(request, session)
        finally:
            session.release()
    return _run_show_with_new_tab(request)


def show_status() -> dict[str, Any]:
    """Whether the last `show` in the shared tab has been acknowledged, without waiting.

    `{"state": "none" | "unacknowledged" | "acknowledged"[, "title": ...]}`. Only a
    report in the shared tab is tracked: a one-off tab (used when the shared one was
    busy) has no listener, so its Acknowledge click is local and cannot be read. The
    last such report is named under `own_tab`, until a later report uses the shared tab.
    """
    status = get_shared_session().report_status()
    if _own_tab_title.value is not None:
        status["own_tab"] = _own_tab_title.value
    return status

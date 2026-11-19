"""The `text-wall-breaker` MCP server.

Browser pages for the user instead of terminal text: `ask` is a richer alternative to
Claude Code's built-in AskUserQuestion, `show` and `review` present a result or a diff, and
`live` carries the conversation of a session that `ask` or `review` started. Each tool's
description is what the agent reads, so it is kept under MAX_TOOL_DESCRIPTION_CHARS.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from functools import partial
from typing import Annotated, Any, Protocol, cast

import anyio
import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .ask import (
    AskRequest,
    BrowserUnavailable,
    InvalidAsk,
    Option,
    Question,
    run_ask,
)
from .descriptions import (
    ASK_DESCRIPTION,
    INSTRUCTIONS,
    LIVE_DESCRIPTION,
    MAX_TOOL_DESCRIPTION_CHARS,
    REVIEW_DESCRIPTION,
    SHOW_DESCRIPTION,
    SHOW_STATUS_DESCRIPTION,
    pp_review_prompt,
)
from .gitdiff import GitDiffError, pages_from_git
from .listener import SessionListener, cancellable
from .live import ATTACH_INSTRUCTION, LiveSession, RepoSource
from .live_common import LiveCall, LiveError, Reply, no_such_session
from .log import configure as configure_logging
from .log import logger
from .opener import find_opener
from .page import ACCENT_ENV_VAR, InvalidAccent, parse_accent
from .results import AskResult, LiveResult, ReviewResult, ShowResult, ShowStatusResult
from .review import (
    InvalidReview,
    Note,
    Page,
    ReviewRequest,
    build_live_html,
    run_review,
)
from .session import live_for
from .show import InvalidShow, ShowRequest, run_show, show_status

__all__ = [
    "MAX_TOOL_DESCRIPTION_CHARS",
    "accent_config_problem",
    "environment_problem",
    "main",
    "server",
]

server = MCPServer(name="text-wall-breaker", instructions=INSTRUCTIONS)


# Claude Code aborts a tool call whose server sends no response and no progress
# notification for its idle window: 30 minutes for a stdio server by default, 5 for a
# network one. A call waiting on the user reports progress well inside that, so only the
# user ends it. Progress does not extend a wall-clock limit (`MCP_TOOL_TIMEOUT`, a
# per-server `timeout`).
KEEPALIVE_SECONDS = 60.0


class _ReportsProgress(Protocol):
    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None: ...


async def _blocking[T](
    ctx: _ReportsProgress | None,
    func: Callable[[], T],
    *,
    cancel: threading.Event | None = None,
) -> T:
    """Run blocking `func` off the event loop, reporting progress until it returns.

    Off the loop because a call owns a socket and a browser tab until the user answers,
    and a second call must be able to open its own tab meanwhile.

    An interrupted call (the client abandons it) sets `cancel` and returns at once. The
    page waits in `listener` see the event through `cancellable` and end; a live session
    gets it as its own argument. Either way the abandoned wait lets go of its tab and
    socket instead of holding them until the user answers.
    """
    event = cancel if cancel is not None else threading.Event()

    def body() -> T:
        with cancellable(event):
            return func()

    async def run() -> T:
        try:
            return await anyio.to_thread.run_sync(body, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            event.set()
            raise

    if ctx is None:
        return await run()

    async def keepalive() -> None:
        beats = 0
        while True:
            await anyio.sleep(KEEPALIVE_SECONDS)
            beats += 1
            try:
                await ctx.report_progress(beats, message="waiting for the user")
            except Exception:
                # The answer still matters more than the heartbeat that failed.
                logger.exception("progress report failed; no more heartbeats for this call")
                return

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(keepalive)
            try:
                result = await run()
            finally:
                group.cancel_scope.cancel()
    except BaseExceptionGroup as error:
        # The task group wraps an error of `func`, which callers catch by its own type
        # (`BrowserUnavailable`, `LiveError`), so a lone one is raised as it was.
        if len(error.exceptions) == 1:
            raise error.exceptions[0] from None
        raise
    return result


def environment_problem() -> str | None:
    """Return why this host cannot show a page, or None if it can.

    Checked once at startup rather than per call: a headless host can never serve an
    `ask`, and failing at launch makes that visible in the MCP client's connection error
    instead of inside the first question the agent asks.
    """
    opener = find_opener()
    return opener if isinstance(opener, str) else None


def accent_config_problem() -> str | None:
    """Return why `TEXT_WALL_BREAKER_ACCENT` can't be used, or None if it's unset or valid.

    Checked once at startup, same reasoning as `environment_problem`: a typo in the
    colour should surface as a connection error the person configuring the server sees
    immediately, not as a page that silently ignored their setting.
    """
    value = os.environ.get(ACCENT_ENV_VAR)
    if not value:
        return None
    try:
        parse_accent(value)
    except InvalidAccent as exc:
        return f"{ACCENT_ENV_VAR} is invalid: {exc}"
    return None


@server.tool(name="ask", description=ASK_DESCRIPTION)
async def ask(
    question: Annotated[
        str,
        Field(description="The question, as Markdown. Leave unset when `questions` is used."),
    ] = "",
    options: Annotated[
        list[Option] | None,
        Field(description="The choices to offer. May be omitted only if allow_free_text is true."),
    ] = None,
    multi_select: Annotated[
        bool, Field(description="Allow more than one option to be selected.")
    ] = False,
    allow_free_text: Annotated[
        bool, Field(description="Show a free-text box alongside the options.")
    ] = True,
    questions: Annotated[
        list[Question] | None,
        Field(
            description="Several questions on one page. Set instead of "
            "question/options/multi_select/allow_free_text; the result is "
            '{"answers": [...]}.'
        ),
    ] = None,
    header: Annotated[
        str,
        Field(
            description="Questions only: Markdown above every question, "
            "typically what earlier rounds settled, so the user answers with it in view. "
            "A long one collapses behind a Context toggle. With a single question, it is "
            "appended to that question as a further paragraph."
        ),
    ] = "",
    resume: Annotated[
        str | None,
        Field(
            description="The `resume` id a cancelled, abandoned or interrupted ask "
            "returned: reopens that ask with the user's earlier answers prefilled. "
            "Takes no other argument. Used once."
        ),
    ] = None,
    live: Annotated[
        bool,
        Field(
            description="Open as a live discussion right away: returns once the page is "
            "up, for `live` calls."
        ),
    ] = False,
    project: Annotated[
        str | None,
        Field(
            description="Folder whose GLOSSARY.md (or CONTEXT.md) defines terms on hover. "
            "Default: the server's working directory, walking up to the git root. Set it "
            "when you work somewhere else, such as a git worktree."
        ),
    ] = None,
    ctx: Context | None = None,
) -> AskResult:
    request = AskRequest(
        question=question,
        options=options or [],
        multi_select=multi_select,
        allow_free_text=allow_free_text,
        questions=questions,
        header=header,
        resume=resume,
        live=live,
        project=project,
    )
    try:
        request.validated()
    except InvalidAsk as exc:
        raise ToolError(str(exc)) from exc

    try:
        # run_ask owns a socket and a browser tab for its whole duration. Off the event
        # loop, so a second ask can open its own tab while this one is still waiting.
        return cast(AskResult, await _blocking(ctx, partial(run_ask, request)))
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the question page: {exc}") from exc


@server.tool(name="show", description=SHOW_DESCRIPTION)
async def show(
    content: Annotated[str, Field(description="What to show, as Markdown.")] = "",
    slides: Annotated[
        list[str] | None,
        Field(
            description="A deck of independent Markdown documents, one "
            "shown at a time with Prev/Next and arrow keys. Set instead of content."
        ),
    ] = None,
    header: Annotated[
        str | None,
        Field(description="Markdown above every slide. Requires slides."),
    ] = None,
    footer: Annotated[
        str | None,
        Field(description="Markdown below every slide. Requires slides."),
    ] = None,
    title: Annotated[str, Field(description="The browser tab's title.")] = "Claude Code",
    save_button: Annotated[
        bool, Field(description="Offer a Save report button (Markdown or HTML).")
    ] = True,
    acknowledge_button: Annotated[
        bool, Field(description="Offer an Acknowledge button marking the report seen.")
    ] = True,
    wait_for_acknowledge: Annotated[
        bool,
        Field(
            description="Block until Acknowledge is clicked, like ask. Requires "
            "acknowledge_button. Left false, check the click later with show_status."
        ),
    ] = False,
    ctx: Context | None = None,
) -> ShowResult:
    request = ShowRequest(
        content=content,
        slides=slides,
        header=header,
        footer=footer,
        title=title,
        save_button=save_button,
        acknowledge_button=acknowledge_button,
        wait_for_acknowledge=wait_for_acknowledge,
    )
    try:
        request.validated()
    except InvalidShow as exc:
        raise ToolError(str(exc)) from exc

    try:
        # Writing and xdg-open are both quick, but off the event loop for the same
        # reason ask's run_ask is: nothing here should block the server's loop.
        return cast(ShowResult, await _blocking(ctx, partial(run_show, request)))
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the page: {exc}") from exc


@server.prompt(
    name="pp-review",
    title="Review the current branch",
    description="Review the current branch against its base in the browser, with your "
    "annotations on the most important changes.",
)
def pp_review(base: str | None = None) -> str:
    return pp_review_prompt(base)


@server.tool(name="show_status", description=SHOW_STATUS_DESCRIPTION)
async def show_status_tool() -> ShowStatusResult:
    return cast(ShowStatusResult, show_status())


@server.tool(name="review", description=REVIEW_DESCRIPTION)
async def review(
    pages: Annotated[
        list[Page] | None,
        Field(description="One entry per file. Set instead of repo."),
    ] = None,
    repo: Annotated[
        str | None,
        Field(
            description="Path of a git repository; the server runs `git diff` in it "
            "and makes one page per changed text file. Set instead of pages."
        ),
    ] = None,
    git_range: Annotated[
        str | None,
        Field(
            description="With repo: what to diff, as `git diff` takes it, e.g. "
            "`main..HEAD`, `abc123^!`. Default `HEAD` (uncommitted changes to tracked files)."
        ),
    ] = None,
    paths: Annotated[
        list[str] | None,
        Field(description="With repo: limit the diff to these paths."),
    ] = None,
    context_lines: Annotated[
        int, Field(description="With repo: unchanged lines around each change (git -U).")
    ] = 3,
    notes: Annotated[
        list[Note] | None,
        Field(
            description="Your remarks on lines the diff shows, to explain why. `line` is the "
            'number in the file (as in the diff\'s `@@` headers), `side` "old" only for a '
            "deleted line. The user reads them inline; a reply comes back as an ordinary "
            "comment on those lines. The notes themselves are not returned."
        ),
    ] = None,
    header: Annotated[
        str | None,
        Field(
            description="Markdown for the top of the page, once. A leading `# Title` stays in "
            "view next to the file, comment and note counts; the rest folds under Details."
        ),
    ] = None,
    title: Annotated[str, Field(description="The browser tab's title.")] = "Claude Code",
    live: Annotated[
        bool,
        Field(
            description="Open as a live session right away: returns once the page is up, "
            "for `live` calls."
        ),
    ] = False,
    ctx: Context | None = None,
) -> ReviewResult:
    if repo is None and (git_range is not None or paths):
        raise ToolError("git_range and paths need repo")
    files: dict[str, str] = {}
    source: RepoSource | None = None
    if repo is not None:
        if pages:
            raise ToolError("set either pages or repo, not both")
        source = RepoSource(
            repo=repo, git_range=git_range, paths=paths, context_lines=context_lines
        )
        try:
            pages, files = await _blocking(ctx, partial(_load_repo, source))
        except GitDiffError as exc:
            raise ToolError(str(exc)) from exc
    request = ReviewRequest(
        pages=pages or [],
        notes=notes or [],
        header=header,
        title=title,
        files=files,
        root=repo,
    )
    try:
        validated = request.validated()
    except InvalidReview as exc:
        raise ToolError(str(exc)) from exc

    def start_live(session: SessionListener, generation: int) -> dict[str, Any]:
        started = LiveSession(
            session,
            validated,
            source,
            build_html=lambda req, generation: build_live_html(req, session, generation),
            load_repo=_load_repo,
            generation=generation,
        )
        return {"live": {"session": started.id}, "instruction": ATTACH_INSTRUCTION}

    try:
        # run_review owns a socket and, ordinarily, a browser tab for its whole
        # duration — off the event loop for the same reason ask's run_ask is.
        return cast(
            ReviewResult,
            await _blocking(ctx, partial(run_review, request, live=live, start_live=start_live)),
        )
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the review page: {exc}") from exc
    except InvalidReview as exc:
        raise ToolError(str(exc)) from exc


def _load_repo(source: RepoSource) -> tuple[list[Page], dict[str, str]]:
    entries = pages_from_git(source.repo, source.git_range, source.paths, source.context_lines)
    pages = [Page(path=entry["path"], diff=entry["diff"]) for entry in entries]
    files = {entry["path"]: entry["text"] for entry in entries if "text" in entry}
    return pages, files


class LiveReply(BaseModel):
    """One message the agent posts in a live session."""

    to: Annotated[str, Field(description='A thread id from the messages, or "chat" for the chat.')]
    text: Annotated[str, Field(description="The message, as Markdown.")]
    done: Annotated[
        bool,
        Field(description="Mark the thread done, a hint for the user to resolve it."),
    ] = False


@server.tool(name="live", description=LIVE_DESCRIPTION)
async def live(
    session: Annotated[str, Field(description="The id `review` or `ask` returned under `live`.")],
    replies: Annotated[list[LiveReply] | None, Field(description="Messages to post.")] = None,
    refresh: Annotated[
        bool,
        Field(
            description="Diff the repository again (a session opened with `repo`) and show "
            "the new diff. Comments follow their lines; those whose lines are gone turn "
            "outdated."
        ),
    ] = False,
    git_range: Annotated[
        str | None, Field(description="A new range for the repo diff; implies refresh.")
    ] = None,
    paths: Annotated[
        list[str] | None, Field(description="New paths for the repo diff; implies refresh.")
    ] = None,
    pages: Annotated[
        list[Page] | None,
        Field(description="Replacement pages by path, for a session opened with `pages`."),
    ] = None,
    notes: Annotated[
        list[Note] | None,
        Field(description="With a refresh: agent notes replacing those of their pages."),
    ] = None,
    question: Annotated[
        str | None,
        Field(description="Ask session with one question: a new question text."),
    ] = None,
    options: Annotated[
        list[Option] | None,
        Field(
            description="Ask session with one question: the new options, replacing all of "
            "them. The page reloads; the user's pick and typed text stay where the option "
            "id still exists."
        ),
    ] = None,
    wait: Annotated[
        bool, Field(description="Block for the user's next message (false: post and return).")
    ] = True,
    ctx: Context | None = None,
) -> LiveResult:
    target = live_for(session)
    if target is None:
        raise ToolError(str(no_such_session(session)))
    cancel = threading.Event()
    call = LiveCall(
        cancel=cancel,
        session_id=session,
        replies=[Reply(to=r.to, text=r.text, done=r.done) for r in replies or []],
        wait=wait,
        refresh=refresh,
        git_range=git_range,
        paths=paths,
        pages=pages,
        notes=notes,
        question=question,
        options=options,
    )
    try:
        return await _blocking(ctx, partial(target.call, call), cancel=cancel)
    except (LiveError, GitDiffError) as exc:
        raise ToolError(str(exc)) from exc


def _reject_unknown_arguments() -> None:
    # MCPServer ignores extra tool arguments; a misnamed one (`reply=` for `replies=`)
    # would otherwise be dropped while the call still succeeds.
    for tool in server._tool_manager.list_tools():
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema(by_alias=True)


_reject_unknown_arguments()


def main() -> None:
    for problem in (environment_problem(), accent_config_problem()):
        if problem is not None:
            print(f"text-wall-breaker: {problem}", file=sys.stderr)
            raise SystemExit(2)
    path = configure_logging()
    logger.info("server start, log %s", path)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

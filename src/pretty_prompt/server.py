"""The `pretty-prompt` MCP server.

One tool, `ask`: a richer alternative to Claude Code's built-in AskUserQuestion. Instead
of rendering options in the terminal panel it opens a real page in the desktop browser,
so a question can carry unlimited options, Markdown bodies and images.

The server name is deliberately broader than its one tool — more tools are expected here
later — but nothing in this module is generalised ahead of a second one existing.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated, Any

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .ask import (
    ALLOW_DEFER_DESCRIPTION,
    SHARE_INTERACTION_SIGNALS,
    AskRequest,
    BrowserUnavailable,
    InvalidAsk,
    Option,
    Question,
    run_ask,
)
from .gitdiff import GitDiffError, pages_from_git
from .opener import find_opener
from .page import ACCENT_ENV_VAR, InvalidAccent, parse_accent
from .review import InvalidReview, Note, Page, ReviewRequest, run_review
from .show import InvalidShow, ShowRequest, run_show, show_status

__all__ = [
    "MAX_TOOL_DESCRIPTION_CHARS",
    "accent_config_problem",
    "environment_problem",
    "main",
    "server",
]

# The MCP client silently truncates a tool's description at this length — a hard prefix
# cut, mid-sentence, with no signal to the agent that anything was cut. `_ASK_DESCRIPTION`
# once exceeded it (4268 chars) and lost everything from partway through `expanded`
# onward (allow_defer, questions, changed_from/visits) invisibly; see
# test_server.py's test_no_tool_description_risks_truncation, which now guards every
# registered tool against a repeat, at a margin below this rather than flush against it.
MAX_TOOL_DESCRIPTION_CHARS = 2048

server = MCPServer(
    name="pretty-prompt",
    instructions=(
        "Browser pages for the user instead of terminal text. `ask` instead of "
        "AskUserQuestion: more than four options, an option that needs more than a line "
        "of explanation, or an answer easier to give while looking at an image. `show`: a "
        "result (report, comparison, long table) that would be a wall of text and needs "
        "no answer. `review`: a reaction that attaches to specific lines of a diff."
    ),
)

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS (see its own comment) rather than
# split across tools/fields, since an agent only ever sees the one description for a
# name it already knows to call.
_ASK_DESCRIPTION = """Ask the user a question in a local browser page; blocks until answered,
cancelled, or timed out.

`question` and option `body` are Markdown. Images: `![alt](/abs/path)` or `file:///abs/path`.
Paragraph breaks are real newlines, not the `\\n` escape.

Keep `question` a short heading (at most 2 lines); detail goes in lists, blockquotes
or option `body`. A `hint` in the result is formatting advice for your next ask.

A long `body` sits behind a Details pill users often skip: put what could flip the
choice in `caveat`.

Returns one of:
  {"selected":[...], "free_text":"...", "expanded":[...]}   answered
  {"cancelled": true} / {"abandoned": true} / {"not_delivered": true}

`selected` and `free_text` are additive. `expanded`: option ids whose body was ever
opened. `not_delivered` (~30s): the user never saw it; ask again. `abandoned`: the
tab was closed with no answer; there is no deadline while it stays open. A cancelled or
abandoned ask carries `resume` when answers were entered: pass it back as `resume` to
reopen the ask prefilled.

`interrupted: true` on an answer: the user stopped the ask there for you to read now.
Its `free_text` may correct your plan or only constrain this answer: do what it says,
no more; its pick counts. The call returns at once; with `questions`, `answers` ends
at the last one given, plus `resume`: use it only if the rest still makes sense.

`questions`: several questions on one page, set instead of
`question`/`options`/etc. Returns `{"answers": [...]}`, one entry per question in
order, carrying the question's `id` when it set one; match by id."""

# Only described when returned, so an agent never reads about fields it won't get.
if SHARE_INTERACTION_SIGNALS:
    _ASK_DESCRIPTION += """

Every answer also has `changed_from` (option ids picked then abandoned) and each
`answers` entry has `visits` (times shown): private, weak signals for shaping what you
ask next."""

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS with margin; see its own comment.
_SHOW_DESCRIPTION = """Show the user a formatted result in a local browser page, for
anything that would be a wall of text in the terminal. Returns at once.

`content` is Markdown, the same subset as `ask` (headings, lists, tables, code,
images, blockquotes). Paragraph breaks are real newlines, not the `\\n` escape.

While the report is unacknowledged, any later call, `show` too, opens in a second tab,
so the report stays up. Before asking about the report, check `show_status` or set
`wait_for_acknowledge`.

Two buttons, both on by default (`save_button`, `acknowledge_button`):
  - Save report: downloads Markdown or HTML, client-side only.
  - Acknowledge: marks it seen. `wait_for_acknowledge` blocks on it like `ask`,
    returning `{"acknowledged": true}` or, if the tab is closed, `{"abandoned": true}`.

Otherwise returns `{"opened": true, "path": "..."}` (`path` only for a one-off tab).
`{"not_delivered": true}`: the page never loaded.

`slides` shows a deck instead of `content`; see its parameter."""

_REVIEW_DESCRIPTION = """Have the user review a diff in a local browser page, one file per page;
blocks until submit, cancel, or the tab is closed. For a reaction that reduces to a choice, use
`ask`.

Source, one of:
  - `repo` (+ optional `git_range`, `paths`, `context_lines`): the server runs
    `git diff` itself. Prefer it for anything in git: passing the diff as arguments
    puts a second copy of it in your context. The user can also show the hidden lines
    between hunks and comment on them; `pages` cannot do that.
  - `pages`: a diff you already hold, one entry per file.

The user comments on a line or a run of lines, can select text inside them, and can
leave a note per file and one overall note. Give `notes` to explain your changes on the
rows the diff shows (a bad anchor is an error); the user reads them inline and may reply.

Returns `{"comments": [...], "page_notes": [{"path", "text"}], "overall_note": "..." |
null}` on Submit (`page_notes` lists only files with a note). Each comment
is `{"path", "side", "line", "text", "context"}`: `side` "old" or "new", `line` that
side's 1-based line number in the file (not its position in the diff text),
`context` that line's text. A run of lines adds `end_side`/`end_line` (its last row;
the two ends may be on different sides) and `context` is every row of it, one per
line, each prefixed `+`, `-` or a space. A text selection adds `quote`, the selected
part. Cancel returns the same shape plus `"cancelled": true`,
keeping what was entered, since a cancel may be deliberate after a partial pass.
The same shape plus `"abandoned": true`: the tab was closed first; comments are
those entered so far. `{"not_delivered": true}`: the page
never loaded."""


def environment_problem() -> str | None:
    """Return why this host cannot show a page, or None if it can.

    Checked once at startup rather than per call: a headless host can never serve an
    `ask`, and failing at launch makes that visible in the MCP client's connection error
    instead of inside the first question the agent asks.
    """
    opener = find_opener()
    return opener if isinstance(opener, str) else None


def accent_config_problem() -> str | None:
    """Return why `PRETTY_PROMPT_ACCENT` can't be used, or None if it's unset or valid.

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


@server.tool(name="ask", description=_ASK_DESCRIPTION)
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
    allow_defer: Annotated[bool, Field(description=ALLOW_DEFER_DESCRIPTION)] = False,
    questions: Annotated[
        list[Question] | None,
        Field(
            description="Several questions on one page. Set instead of "
            "question/options/multi_select/allow_free_text/allow_defer; the result is "
            '{"answers": [...]}.'
        ),
    ] = None,
    header: Annotated[
        str,
        Field(
            description="Questions only: Markdown above every question, "
            "typically what earlier rounds settled, so the user answers with it in view. "
            "A long one collapses behind a Context toggle. A single question carries its "
            "context in question itself."
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
) -> dict[str, Any]:
    request = AskRequest(
        question=question,
        options=options or [],
        multi_select=multi_select,
        allow_free_text=allow_free_text,
        allow_defer=allow_defer,
        questions=questions,
        header=header,
        resume=resume,
    )
    try:
        request.validated()
    except InvalidAsk as exc:
        raise ToolError(str(exc)) from exc

    try:
        # run_ask owns a socket and a browser tab for its whole duration. Off the event
        # loop, so a second ask can open its own tab while this one is still waiting.
        return await anyio.to_thread.run_sync(run_ask, request)
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the question page: {exc}") from exc


@server.tool(name="show", description=_SHOW_DESCRIPTION)
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
) -> dict[str, Any]:
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
        return await anyio.to_thread.run_sync(run_show, request)
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the page: {exc}") from exc


_SHOW_STATUS_DESCRIPTION = """Whether the last `show` was acknowledged, without blocking: check it
before asking about a report shown without `wait_for_acknowledge`.

Returns `{"state": "unacknowledged" | "acknowledged" | "none", "title": "..."}`.
`none`: no report in the shared tab, or an `ask`/`review` has replaced it since. A
report that opened in its own one-off tab (shared tab busy, or holding an unread
report) is not tracked: the result still describes the shared tab's report, or reads
`none`. `title` tells you which report it is."""


@server.tool(name="show_status", description=_SHOW_STATUS_DESCRIPTION)
async def show_status_tool() -> dict[str, Any]:
    return show_status()


@server.tool(name="review", description=_REVIEW_DESCRIPTION)
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
            description="Your remarks on lines the diff shows, to explain why. The user "
            "reads them inline and can reply with a comment; they are not returned."
        ),
    ] = None,
    header: Annotated[str | None, Field(description="Markdown shown above every page.")] = None,
    title: Annotated[str, Field(description="The browser tab's title.")] = "Claude Code",
) -> dict[str, Any]:
    if repo is None and (git_range is not None or paths):
        raise ToolError("git_range and paths need repo")
    files: dict[str, str] = {}
    if repo is not None:
        if pages:
            raise ToolError("set either pages or repo, not both")
        try:
            entries = await anyio.to_thread.run_sync(
                pages_from_git, repo, git_range, paths, context_lines
            )
        except GitDiffError as exc:
            raise ToolError(str(exc)) from exc
        pages = [Page(path=entry["path"], diff=entry["diff"]) for entry in entries]
        files = {entry["path"]: entry["text"] for entry in entries if "text" in entry}
    request = ReviewRequest(
        pages=pages or [],
        notes=notes or [],
        header=header,
        title=title,
        files=files,
    )
    try:
        request.validated()
    except InvalidReview as exc:
        raise ToolError(str(exc)) from exc

    try:
        # run_review owns a socket and, ordinarily, a browser tab for its whole
        # duration — off the event loop for the same reason ask's run_ask is.
        return await anyio.to_thread.run_sync(run_review, request)
    except BrowserUnavailable as exc:
        raise ToolError(f"could not open the review page: {exc}") from exc


def main() -> None:
    for problem in (environment_problem(), accent_config_problem()):
        if problem is not None:
            print(f"pretty-prompt: {problem}", file=sys.stderr)
            raise SystemExit(2)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

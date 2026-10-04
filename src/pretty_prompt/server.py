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
from .opener import find_opener
from .page import ACCENT_ENV_VAR, InvalidAccent, parse_accent
from .review import InvalidReview, Page, ReviewRequest, run_review
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
# onward (allow_defer, questions, streamed ask, changed_from/visits) invisibly; see
# test_server.py's test_no_tool_description_risks_truncation, which now guards every
# registered tool against a repeat, at a margin below this rather than flush against it.
MAX_TOOL_DESCRIPTION_CHARS = 2048

server = MCPServer(
    name="pretty-prompt",
    instructions=(
        "Ask the user a question, show them a formatted result, or have them review an "
        "artifact, in a browser page instead of the terminal. Use `ask` over "
        "AskUserQuestion when the question has more than four options, when an option "
        "needs more than a line of explanation, or when the answer is easier to give "
        "while looking at an image. Use `show` when a result — a report, a comparison, "
        "a long table — would otherwise be a wall of text in the terminal and doesn't "
        "need an answer back. Use `review` when the user's reaction needs to attach to "
        "specific lines of a diff, not just a discrete choice."
    ),
)

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS (see its own comment) rather than
# split across tools/fields, since an agent only ever sees the one description for a
# name it already knows to call.
_ASK_DESCRIPTION = """Ask the user a question in a local browser page; blocks until answered,
cancelled, or timed out. Use over AskUserQuestion for >4 options, a Markdown option
body, or an image.

`question`/`body` are Markdown, not HTML. Images: `![alt](file:///abs/path)`.
Paragraph breaks are real newlines, not the two-character `\\n` escape (recovered
either way, but write a real one).

A long `body` sits behind a click users often skip. Anything that could flip the
choice goes in `caveat` instead — always visible, used sparingly.

Returns one of:
  {"selected":[...], "free_text":"...", "expanded":[...]}   answered
  {"answers":[{selected, free_text, expanded}, ...]}         answered `questions`
  {"cancelled": true} / {"timed_out": true} / {"not_delivered": true}

`not_delivered` (~30s): user never saw it, ask again rather than retry longer.
`timed_out`: seen, unanswered. Either also carries `resume` if answers were entered:
pass it back as `resume` to reopen the ask prefilled.

`selected`/`free_text` are additive. `expanded`: option ids whose body was ever
opened, even if closed again.

`allow_defer`: user may answer "not yet" → `{"deferred": true, "selected": [],
"free_text": <reason>}`.

`questions` (prototype): several questions on one page, instead of
`question`/`options`/etc, never both. Returns `{"answers": [...]}`, one
`{selected, free_text, expanded}` per question, in order; a question's optional `id`
is echoed on its entry, so match by id.

Streamed ask (prototype, `questions`+`more_coming`): send in chunks so the user
starts answering while you write the rest. First call returns `{"ask_id",
"answers_so_far": []}`; later calls add `questions` under that id; the final call
(no `more_coming`) waits and returns `{"answers": [...]}` or `{"cancelled": true}`.
Skip this for a short ask."""

# Only described when returned, so an agent never reads about fields it won't get.
if SHARE_INTERACTION_SIGNALS:
    _ASK_DESCRIPTION += """

Every answer also has `changed_from` (option ids picked then abandoned) and each
`answers` entry has `visits` (times shown). Weak signals for what to ask next —
never quote them back to the user."""

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS with margin — see its own comment.
_SHOW_DESCRIPTION = """Show the user a formatted result in a local browser page (prototype).

Opens a page with `content` as Markdown; returns immediately, no answer to wait
for. Use instead of a long terminal reply for a report, comparison, table, or
anything that would be a wall of text — same Markdown subset as `ask` (headings,
lists, tables, code, images, blockquotes), and a paragraph break is a real newline,
not the two-character `\\n` escape.

Shares a tab with `ask`. An unacknowledged report is never replaced by an `ask` or
`review`: that opens a second tab instead. Once acknowledged, or its tab is closed, the
next `ask` reuses the tab and the report is gone. Poll `show_status`, or set
`wait_for_acknowledge` when a question about the report follows.

Two buttons, on by default:
  - Save report — downloads the content as Markdown or the rendered HTML. Client-side
    only, never reported back.
  - Acknowledge — marks it seen. `wait_for_acknowledge` blocks on it (like `ask`),
    returning `{"acknowledged": true}` or `{"timed_out": true}`. Left false (default),
    the click is only visible through `show_status`.
Turn either off with `save_button`/`acknowledge_button` when it doesn't apply.

Without `wait_for_acknowledge`, returns `{"opened": true, "path": "..."}` (path only
for a one-off tab) once the browser has it. `{"not_delivered": true}` can come back
instead whether or not `wait_for_acknowledge` is set — it means the page never loaded.

`slides` makes a deck instead — independent Markdown documents, one at a time, with
Prev/Next and arrow-key nav. Set instead of `content`, never alongside it.
`header`/`footer` are one more Markdown block each, shown above/below every slide —
only meaningful with `slides`."""

_REVIEW_DESCRIPTION = """Have the user review a diff in a local browser page and wait for comments.

Opens one file's diff per navigable page (Prev/Next and arrow keys, same as
`show`'s deck mode), then blocks until submit, cancel, or timeout. Use this over
`ask` when the reaction needs to attach to specific lines of a diff rather than
reduce to a discrete choice — e.g. reviewing code changes. For a question with
options, use `ask`.

Each `pages` entry is one file: `path`, `diff` (raw unified diff text, e.g. `git
diff` output — a `@@ -a,b +c,d @@` hunk header is required; a diff with none is
refused with an error before any page opens), and an optional `blurb` (Markdown,
shown atop that page only, e.g. why it changed). `header` is one more Markdown
block, shown above every page.

Clicking a diff line opens a text box anchored to it (a bubble icon marks a line as
commentable on hover). Comments are single-line only, no range selection. An
"Overall note" box below the pages holds one free-text remark for the whole call.

Returns `{"comments": [...], "overall_note": "..." | null}` on Submit — one
`{"path", "side", "line", "text", "context"}` per comment, `side` "old"/"new",
`line` that side's 1-based number in `path` (resolved from the diff, not its
position in the text), `context` that line's own text. Cancel returns the same
shape plus `"cancelled": true` — comments/note already left are kept, not
discarded, since a cancel may be deliberate after a partial pass. `{"timed_out":
true}`: nothing arrived in time. `{"not_delivered": true}`: the page never loaded,
same ~30s window as `ask`."""


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
    timeout_seconds: Annotated[
        float,
        Field(
            description="Give up after this many seconds without hearing from the page. "
            "The page keeps the call alive while it is visible, however long the user "
            "reads, so this only runs while the tab is hidden or closed. The default "
            "suits long multi-question pages too; there is no need to raise it for them."
        ),
    ] = 300.0,
    questions: Annotated[
        list[Question] | None,
        Field(
            description="Prototype: ask several questions on one page instead of one. "
            "When set, question/options/multi_select/allow_free_text/allow_defer are "
            "ignored, and "
            'the result is {"answers": [...]} instead of {"selected", "free_text"}.'
        ),
    ] = None,
    header: Annotated[
        str,
        Field(
            description="Prototype. Requires questions: with a single question, "
            "header is rejected, so put the context in question. Markdown shown above "
            "every question of the page, typically what earlier rounds already settled, "
            "so the user answers with it in view. A long one collapses behind a "
            "Context toggle."
        ),
    ] = "",
    more_coming: Annotated[
        bool,
        Field(
            description="Prototype, questions only: more questions follow in later "
            "calls. The page opens with these and the call returns {ask_id, "
            "answers_so_far} at once, so the user starts answering while you write the "
            "rest. See the streamed ask section of this tool's description."
        ),
    ] = False,
    resume: Annotated[
        str | None,
        Field(
            description="The `resume` id a cancelled or timed-out ask returned: reopens "
            "that ask with the user's earlier answers prefilled. Only timeout_seconds "
            "applies. Used once."
        ),
    ] = None,
    ask_id: Annotated[
        str | None,
        Field(
            description="Prototype: the ask_id a more_coming call returned. Appends "
            "questions to that open page instead of opening a new one; only questions, "
            "more_coming and timeout_seconds apply."
        ),
    ] = None,
) -> dict[str, Any]:
    request = AskRequest(
        question=question,
        options=options or [],
        multi_select=multi_select,
        allow_free_text=allow_free_text,
        allow_defer=allow_defer,
        timeout_seconds=timeout_seconds,
        questions=questions,
        header=header,
        more_coming=more_coming,
        ask_id=ask_id,
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
            description="Prototype: show a deck instead of one document — several "
            "independent Markdown documents, one shown at a time with Prev/Next and "
            "arrow-key navigation. Set instead of content, never alongside it."
        ),
    ] = None,
    header: Annotated[
        str | None,
        Field(description="Markdown shown above the slide on every slide. Requires slides."),
    ] = None,
    footer: Annotated[
        str | None,
        Field(description="Markdown shown below the slide on every slide. Requires slides."),
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
            "acknowledge_button. Default false: show returns immediately; check the "
            "click later with show_status."
        ),
    ] = False,
    timeout_seconds: Annotated[
        float,
        Field(description="With wait_for_acknowledge, give up after this many idle seconds."),
    ] = 300.0,
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
        timeout_seconds=timeout_seconds,
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


_SHOW_STATUS_DESCRIPTION = """Check, without blocking, whether the last `show` was acknowledged.

Returns `{"state": "unacknowledged" | "acknowledged" | "none", "title": "..."}`.
`none`: no report was shown in the shared tab, or an `ask`/`review` has since replaced
it. Use it to find out if a report was read before asking about it, when
`wait_for_acknowledge` was left false. A report that fell back to its own one-off tab
(shared tab busy) is not tracked and reads `none`."""


@server.tool(name="show_status", description=_SHOW_STATUS_DESCRIPTION)
async def show_status_tool() -> dict[str, Any]:
    return show_status()


@server.tool(name="review", description=_REVIEW_DESCRIPTION)
async def review(
    pages: Annotated[
        list[Page],
        Field(
            description="One entry per file: path, diff (raw unified diff text), "
            "and an optional blurb (Markdown, shown at the top of that page only)."
        ),
    ],
    header: Annotated[str | None, Field(description="Markdown shown above every page.")] = None,
    title: Annotated[str, Field(description="The browser tab's title.")] = "Claude Code",
    timeout_seconds: Annotated[
        float,
        Field(
            description="Give up after this many seconds without hearing from the "
            "page. The page keeps the call alive while it is visible, however long the "
            "user takes to review, so this only runs while the tab is hidden or closed."
        ),
    ] = 300.0,
) -> dict[str, Any]:
    request = ReviewRequest(
        pages=pages,
        header=header,
        title=title,
        timeout_seconds=timeout_seconds,
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

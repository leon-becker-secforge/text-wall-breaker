"""The `pretty-prompt` MCP server.

One tool, `ask`: a richer alternative to Claude Code's built-in AskUserQuestion. Instead
of rendering options in the terminal panel it opens a real page in the desktop browser,
so a question can carry unlimited options, Markdown bodies and images.

The server name is deliberately broader than its one tool — more tools are expected here
later — but nothing in this module is generalised ahead of a second one existing.
"""

from __future__ import annotations

import os
import shutil
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
from .page import ACCENT_ENV_VAR, InvalidAccent, parse_accent
from .show import InvalidShow, ShowRequest, run_show

__all__ = ["accent_config_problem", "environment_problem", "main", "server"]

server = MCPServer(
    name="pretty-prompt",
    instructions=(
        "Ask the user a question, or show them a formatted result, in a browser page "
        "instead of the terminal. Use `ask` over AskUserQuestion when the question has "
        "more than four options, when an option needs more than a line of explanation, "
        "or when the answer is easier to give while looking at an image. Use `show` "
        "when a result — a report, a comparison, a diff, a long table — would otherwise "
        "be a wall of text in the terminal and doesn't need an answer back."
    ),
)

_ASK_DESCRIPTION = """Ask the user a question in a local browser page and wait for the answer.

Opens a page on the user's desktop, then blocks until they answer, cancel, or the timeout
elapses. Use instead of AskUserQuestion when the question has more than four options, when
options need Markdown bodies, or when it should show an image.

`question` and each option's `body` are Markdown. Raw HTML is not rendered. Images use
Markdown image syntax with an absolute file:// URL, e.g. `![diagram](file:///tmp/x.png)`.
A paragraph break is a real newline in the string you pass, same as any other Markdown
field here — not the two-character `\n` escape sequence, which prints as literal text if
typed outside a fenced code block (it is still recovered as a line break, but a real
newline is what to write).

A long body sits behind a click the user often never makes, even for the option they
pick. Anything that could flip the choice (a precondition, a cost, a conflict with an
earlier answer) goes in the option's `caveat`, which is always visible, not in `body`.
Use it sparingly: most options need none, and a caveat on every option drowns out the
one that matters.

Returns exactly one of:
  {"selected": [...], "free_text": "...", "expanded": [...]}  the user answered
  {"answers": [{"selected": [...], "free_text": "...", "expanded": [...]}, ...]}
                                                             answered `questions`
  {"cancelled": true}                                      the user pressed Cancel
  {"timed_out": true}                                      nothing arrived in time
  {"not_delivered": true}                                  the page never loaded

`not_delivered` comes back within about 30 seconds and means the user never saw the
question: ask again in the terminal instead of retrying with a longer timeout.
`timed_out` means the page did load but went unanswered.

Selecting an option and writing free text are additive, not exclusive, so `selected` and
`free_text` can both be populated. `expanded` is the ids of options whose Markdown body
was opened (read) at any point before the answer was sent, for options long enough to
collapse behind a drawer — it stays set even if the drawer was closed again afterwards,
so an id can be selected without being expanded, or expanded without being selected.

With `allow_defer`, the user may answer "not yet" instead: that answer carries
`"deferred": true`, an empty `selected`, and the reason, if any, in `free_text`.

`questions` (prototype) puts several questions on one page instead of one — set it
instead of `question`/`options`/`multi_select`/`allow_free_text`/`allow_defer`, never
alongside them.
The user answers all of them before sending, and the result's `answers` list is in the
same order, one `{selected, free_text, expanded}` per question."""

_ASK_DESCRIPTION += """

Streamed ask (prototype): with three or more questions, or long bodies, don't make the
user wait while you write them all. Send the first question(s) with `more_coming: true`;
the page opens and the call returns {"ask_id", "answers_so_far": []} at once. Then call
again with that `ask_id`, the next question(s) and `more_coming: true` for each further
chunk; the page grows in place, and each such call returns the answers to the questions
the user has reached so far (the current one may still be blank or change). Use them only
to adjust the questions you haven't sent yet. The last call sets `ask_id` and leaves
`more_coming` unset (its `questions` may be empty); only it waits, and it returns the
usual {"answers": [...]} for every question sent. A call may return {"cancelled": true}
instead: then stop, and send nothing more. Short asks stay a single call.
If your `ask` has no `more_coming` parameter, your session loaded this tool before
streaming existed; it takes a session started after the update."""

# Only described when returned, so an agent never reads about fields it won't get.
if SHARE_INTERACTION_SIGNALS:
    _ASK_DESCRIPTION += """

Every answer also has `changed_from`: ids picked at some point and then abandoned (not in
`selected`), in the order first picked. Each entry of `answers` also has `visits`: how
often that question was shown, 1 if the user never came back to it. These are weak
hints about how the answer was reached. Use them to decide what to ask next, e.g. a
follow-up on the option given up; never quote them back to the user or read feelings
into them."""

_SHOW_DESCRIPTION = """Show the user a formatted result in a local browser page (prototype).

Opens a page with `content` rendered as Markdown. By default returns immediately —
there's no answer to wait for. Use this instead of a long terminal reply when the
content is a report, a comparison, a diff, a long table, or anything else that would
otherwise be a wall of text: the same Markdown subset `ask` renders (headings, lists,
tables, code, images, blockquotes) applies here too, including that a paragraph break is
a real newline, not a typed `\n` escape.

Reuses the same browser tab `ask` uses, so a `show` and an `ask` in the same session
don't pile up separate tabs.

The page carries two buttons, on by default:
  - Save report — lets the user download the content, as the original Markdown or as
    the rendered HTML page. Purely client-side; never reported back here.
  - Acknowledge — marks the report as seen. Set `wait_for_acknowledge` to actually block
    on it (like `ask`), which then returns `{"acknowledged": true}` or, if nothing
    arrives before `timeout_seconds`, `{"timed_out": true}`. Left false (the default),
    clicking it is a local-only mark for the user, not reported back.
Turn either off with `save_button`/`acknowledge_button` when it doesn't apply — e.g. no
Acknowledge button on a page that's purely informational and never needs confirming.

Without `wait_for_acknowledge`, returns `{"opened": true, "path": "..."}` (path is only
present for a one-off, non-reused tab) as soon as the browser has the page. Either way,
`{"not_delivered": true}` means the page never loaded, so the user hasn't seen it.

`slides` puts the content in a deck instead — several independent Markdown documents
shown one at a time with Prev/Next buttons and arrow-key navigation, a "N / total"
position indicator between them. Set it instead of `content`, never alongside it.
`header`/`footer` are each one more Markdown document, shown above/below the slide on
every slide — only meaningful alongside `slides`."""


def environment_problem() -> str | None:
    """Return why this host cannot show a page, or None if it can.

    Checked once at startup rather than per call: a headless host can never serve an
    `ask`, and failing at launch makes that visible in the MCP client's connection error
    instead of inside the first question the agent asks.
    """
    if shutil.which("xdg-open") is None:
        return "xdg-open is not on PATH — pretty-prompt needs it to open the question page"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return "neither DISPLAY nor WAYLAND_DISPLAY is set — there is no desktop to open a page on"
    return None


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
            description="Prototype, questions only: Markdown shown above every question "
            "of the page, typically what earlier rounds already settled, so the user "
            "answers with it in view. A long one collapses behind a Context toggle."
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
            "acknowledge_button. Default false: show returns immediately and a click "
            "is a local-only mark, never reported back."
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


def main() -> None:
    for problem in (environment_problem(), accent_config_problem()):
        if problem is not None:
            print(f"pretty-prompt: {problem}", file=sys.stderr)
            raise SystemExit(2)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

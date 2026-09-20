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

from .ask import AskRequest, BrowserUnavailable, InvalidAsk, Option, Question, run_ask
from .page import ACCENT_ENV_VAR, InvalidAccent, parse_accent

__all__ = ["accent_config_problem", "environment_problem", "main", "server"]

server = MCPServer(
    name="pretty-prompt",
    instructions=(
        "Ask the user a question in a browser page instead of the terminal. Prefer this "
        "over AskUserQuestion when the question has more than four options, when an "
        "option needs more than a line of explanation, or when the answer is easier to "
        "give while looking at an image."
    ),
)

_ASK_DESCRIPTION = """Ask the user a question in a local browser page and wait for the answer.

Opens a page on the user's desktop, then blocks until they answer, cancel, or the timeout
elapses. Use instead of AskUserQuestion when the question has more than four options, when
options need Markdown bodies, or when it should show an image.

`question` and each option's `body` are Markdown. Raw HTML is not rendered. Images use
Markdown image syntax with an absolute file:// URL, e.g. `![diagram](file:///tmp/x.png)`.

Returns exactly one of:
  {"selected": [...], "free_text": "...", "expanded": [...]}  the user answered
  {"answers": [{"selected": [...], "free_text": "...", "expanded": [...]}, ...]}
                                                             answered `questions`
  {"cancelled": true}                                      the user pressed Cancel
  {"timed_out": true}                                      nothing arrived in time

Selecting an option and writing free text are additive, not exclusive, so `selected` and
`free_text` can both be populated. `expanded` is the ids of options whose Markdown body
was opened (read) at any point before the answer was sent, for options long enough to
collapse behind a drawer — it stays set even if the drawer was closed again afterwards,
so an id can be selected without being expanded, or expanded without being selected.

`questions` (prototype) puts several questions on one page instead of one — set it
instead of `question`/`options`/`multi_select`/`allow_free_text`, never alongside them.
The user answers all of them before sending, and the result's `answers` list is in the
same order, one `{selected, free_text, expanded}` per question."""


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
    timeout_seconds: Annotated[
        float, Field(description="Give up after this many seconds with no activity on the page.")
    ] = 300.0,
    questions: Annotated[
        list[Question] | None,
        Field(
            description="Prototype: ask several questions on one page instead of one. "
            "When set, question/options/multi_select/allow_free_text are ignored, and "
            'the result is {"answers": [...]} instead of {"selected", "free_text"}.'
        ),
    ] = None,
) -> dict[str, Any]:
    request = AskRequest(
        question=question,
        options=options or [],
        multi_select=multi_select,
        allow_free_text=allow_free_text,
        timeout_seconds=timeout_seconds,
        questions=questions,
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


def main() -> None:
    for problem in (environment_problem(), accent_config_problem()):
        if problem is not None:
            print(f"pretty-prompt: {problem}", file=sys.stderr)
            raise SystemExit(2)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

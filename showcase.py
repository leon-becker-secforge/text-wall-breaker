#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "mcp>=2.2,<3",
#   "typer",
#   "rich",
# ]
# ///
"""Start the pretty-prompt MCP server from this checkout and call each tool with every feature.

Each call opens a page in the browser; calls that block (`ask`, `review`, `show` with
`wait_for_acknowledge`) wait for the page before the next one starts. Results print to
the terminal.
"""

import asyncio
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from mcp import Client, StdioServerParameters
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

WORKSPACE = Path(__file__).resolve().parent.parent

app = typer.Typer(add_completion=False)
console = Console()


@contextmanager
def status_spinner(text: str) -> Iterator[Console]:
    with console.status(f"[cyan]{text}[/cyan]"):
        yield console
    console.print(f"[green]✓[/green] {text}")


def _server(accent: str | None) -> StdioServerParameters:
    # The client passes only a handful of variables by default; the server needs
    # DISPLAY/WAYLAND_DISPLAY and any PRETTY_PROMPT_* override to open a page. This
    # script's own VIRTUAL_ENV would make `uv run` warn about the workspace's .venv.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if accent:
        env["PRETTY_PROMPT_ACCENT"] = accent
    return StdioServerParameters(
        command="uv",
        args=[
            "run",
            "--directory",
            str(WORKSPACE),
            "--package",
            "pretty-prompt",
            "pretty-prompt-mcp",
        ],
        env=env,
    )


def _payload(structured: dict[str, Any] | None) -> dict[str, Any]:
    # MCPServer wraps a dict return under "result" in structured content.
    structured = structured or {}
    return structured.get("result", structured)


async def _call(client: Client, step: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    rprint(Panel(f"[bold]{tool}[/bold] · {step}", border_style="green", expand=False))
    with status_spinner(f"{tool}: {step}"):
        result = await client.call_tool(tool, arguments)
    if result.is_error:
        text = " ".join(getattr(block, "text", "") for block in result.content)
        rprint(f"[red]{tool} failed:[/red] {text}")
        raise typer.Exit(1)
    payload = _payload(result.structured_content)
    console.print(Syntax(json.dumps(payload, indent=2), "json", theme="ansi_dark"))
    return payload


def _images(tmp: Path) -> dict[str, str]:
    """Write small SVGs to `tmp` and return their file:// URLs by name."""
    shapes = {
        "cards": '<rect x="20" y="20" width="120" height="80" rx="8" fill="#16a34a"/>'
        '<rect x="160" y="20" width="120" height="80" rx="8" fill="#86efac"/>',
        "list": "".join(
            f'<rect x="20" y="{20 + i * 28}" width="260" height="20" rx="4" fill="#4ade80"/>'
            for i in range(4)
        ),
        "arch": '<circle cx="60" cy="60" r="36" fill="#15803d"/>'
        '<line x1="96" y1="60" x2="204" y2="60" stroke="#15803d" stroke-width="4"/>'
        '<circle cx="240" cy="60" r="36" fill="#bbf7d0"/>',
    }
    urls = {}
    for name, body in shapes.items():
        path = tmp / f"{name}.svg"
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="130">'
            f'<rect width="300" height="130" fill="#f0fdf4"/>{body}</svg>'
        )
        urls[name] = path.as_uri()
    return urls


MARKDOWN_TOUR = """\
# Markdown tour

Paragraphs with **bold**, *italic*, ~~strikethrough~~, `inline code` and a
[link](https://example.com). Raw HTML is escaped: <script>alert(1)</script>

## Lists

- unordered
  - nested by indentation
1. ordered
2. second

> A blockquote.

## Table

| file | severity | lines |
|:--|:-:|--:|
| ask.py | high | 12 |
| page.py | low | 3 |

## Code

```python
def answer() -> int:
    return 42
```

```diff
-    return 1
+    return 2
```

```mermaid
graph LR
  agent -->|ask| server --> browser --> user
```
"""

DIFF_ASK = """\
--- a/src/app/config.py
+++ b/src/app/config.py
@@ -1,6 +1,7 @@
 import os

-TIMEOUT = 30
+TIMEOUT = 60
+RETRIES = 3

 def load():
     return os.environ.get("APP_ENV", "dev")
"""

DIFF_PAGE = """\
--- a/README.md
+++ b/README.md
@@ -3,4 +3,4 @@
 ## Usage

-Run `app start`.
+Run `app start --env prod`.

"""


async def showcase_ask(client: Client, images: dict[str, str]) -> None:
    # Single question: options with body, caveat, recommended, a long collapsing body,
    # an image in the question, free text and defer.
    await _call(
        client,
        "single question, every option feature",
        "ask",
        {
            "question": f"Which backend?\n\n![architecture]({images['arch']})",
            "options": [
                {
                    "id": "pg",
                    "label": "PostgreSQL",
                    "recommended": True,
                    "caveat": "Needs the `pg` operator installed.",
                    "body": "Ships with the **operator**, so no extra service.\n\n"
                    + "Long body. " * 50,
                },
                {"id": "sqlite", "label": "SQLite", "body": "Single file, no server."},
                {
                    "id": "code",
                    "label": "Custom store",
                    "body": "A body with a fenced block collapses behind Details:\n\n"
                    "```python\nstore = Store()\n```",
                },
                {"id": "none", "label": "Undecided"},
            ],
            "allow_free_text": True,
            "allow_defer": True,
        },
    )

    # Multi-select with prefilled picks, and an image gallery (2+ image-bearing options).
    await _call(
        client,
        "multi_select, prefill, image gallery",
        "ask",
        {
            "question": "Which layouts should ship? Images open full size on click.",
            "options": [
                {
                    "id": "cards",
                    "label": "Cards",
                    "selected": True,
                    "recommended": True,
                    "body": f"![cards]({images['cards']})",
                },
                {"id": "list", "label": "List", "body": f"![list]({images['list']})"},
            ],
            "multi_select": True,
            "allow_free_text": False,
        },
    )

    # Free text only.
    await _call(
        client,
        "free text only",
        "ask",
        {
            "question": "Anything else the service should do?",
            "allow_free_text": True,
        },
    )

    # Several questions as a wizard, with header, ids, per-question defer and prefill.
    # "Stop here" on any step returns `interrupted: true` and a `resume` id.
    result = await _call(
        client,
        "questions wizard with header",
        "ask",
        {
            "header": "Settled so far:\n\n- backend: PostgreSQL\n- layout: cards\n\n"
            + MARKDOWN_TOUR,
            "questions": [
                {
                    "id": "region",
                    "question": "Which region?",
                    "allow_defer": True,
                    "options": [
                        {"id": "eu", "label": "eu-central-1", "recommended": True},
                        {"id": "us", "label": "us-east-1"},
                    ],
                },
                {
                    "id": "plan",
                    "question": "Which plan?",
                    "multi_select": True,
                    "options": [
                        {"id": "base", "label": "Base", "selected": True},
                        {"id": "backup", "label": "Backups", "caveat": "Adds **€20/month**."},
                        {"id": "ha", "label": "High availability"},
                    ],
                },
                {"id": "name", "question": "Service name?", "free_text": "orders-api"},
            ],
        },
    )
    if "resume" in result:
        rprint("[yellow]Ended early with answers entered; reopening via resume.[/yellow]")
        await _call(client, "resume prefilled", "ask", {"resume": result["resume"]})


async def showcase_show(client: Client) -> None:
    await _call(client, "show_status before any show", "show_status", {})

    # Without waiting: returns at once; the Acknowledge click is read via show_status.
    report = await _call(
        client,
        "markdown report, returns at once",
        "show",
        {
            "content": MARKDOWN_TOUR + "\n> Press **Acknowledge** to continue the showcase.",
            "title": "Showcase: report",
        },
    )
    await _call(client, "show_status right after", "show_status", {})

    # The next show would replace the report, so poll show_status until it is read.
    # Capped at 10 minutes so a tab closed unread does not stall the showcase.
    state = "unacknowledged"
    if "not_delivered" not in report:
        with status_spinner("Polling show_status until Acknowledge"):
            for _ in range(600):
                result = await client.call_tool("show_status", {})
                state = _payload(result.structured_content).get("state", "none")
                if state != "unacknowledged":
                    break
                await asyncio.sleep(1)
    rprint(f"show_status: [bold]{state}[/bold]")

    # Republishing to the open shared tab updates it live. Each update sets
    # acknowledge_button False: an Acknowledge button would mark the tab as holding an
    # unread report, and the next show would then open a new tab instead of updating it.
    for step in range(1, 4):
        await asyncio.sleep(2)
        await _call(
            client,
            f"live update {step}/3",
            "show",
            {
                "content": f"# Progress\n\nStep **{step}** of 3 done.",
                "title": "Showcase: progress",
                "save_button": False,
                "acknowledge_button": False,
            },
        )

    await _call(
        client,
        "slides with header and footer, waits for Acknowledge",
        "show",
        {
            "slides": [
                "# Slide 1\n\nPrev/Next and arrow keys move between slides.",
                "# Slide 2\n\n| a | b |\n|---|---|\n| 1 | 2 |",
                "# Slide 3\n\nPress **Acknowledge** to continue the showcase.",
            ],
            "header": "_Showcase deck_",
            "footer": "Footer on every slide.",
            "title": "Showcase: slides",
            "wait_for_acknowledge": True,
        },
    )
    await _call(client, "show_status after Acknowledge", "show_status", {})


async def showcase_review(client: Client) -> None:
    await _call(
        client,
        "pages with blurbs and header",
        "review",
        {
            "header": "Reviewing a sample change: click any line to comment.",
            "title": "Showcase: review",
            "pages": [
                {"path": "src/app/config.py", "diff": DIFF_ASK, "blurb": "Raised the **timeout**."},
                {"path": "README.md", "diff": DIFF_PAGE},
            ],
        },
    )
    await _call(
        client,
        "repo mode: the last commit of this checkout",
        "review",
        {
            "repo": str(WORKSPACE),
            "git_range": "HEAD~1..HEAD",
            "paths": ["pretty-prompt"],
            "context_lines": 5,
            "header": "The server ran `git diff HEAD~1..HEAD -U5 -- pretty-prompt` itself.",
        },
    )


async def _run(tools: list[str], accent: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="pretty-prompt-showcase-") as tmp:
        images = _images(Path(tmp))
        rprint("[cyan]Starting pretty-prompt server from this checkout")
        async with Client(_server(accent)) as client:
            if "ask" in tools:
                await showcase_ask(client, images)
            if "show" in tools:
                await showcase_show(client)
            if "review" in tools:
                await showcase_review(client)


@app.command()
def run(
    tool: Annotated[
        list[str] | None, typer.Argument(help="ask, show, review; all when omitted.")
    ] = None,
    accent: Annotated[
        str | None, typer.Option(help="PRETTY_PROMPT_ACCENT for the server, e.g. '#2563eb'.")
    ] = None,
) -> None:
    """Start the server and walk each tool through its features."""
    tools = tool or ["ask", "show", "review"]
    unknown = set(tools) - {"ask", "show", "review"}
    if unknown:
        rprint(f"[red]Unknown tool(s): {', '.join(sorted(unknown))}")
        raise typer.Exit(2)
    asyncio.run(_run(tools, accent))


if __name__ == "__main__":
    app()

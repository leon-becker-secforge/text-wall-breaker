#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "mcp>=2.2,<3",
#   "typer",
#   "rich",
# ]
# ///
"""Start the text-wall-breaker MCP server from this checkout and call each tool with every feature.

Each call opens a page in the browser; calls that block (`ask`, `review`, `show` with
`wait_for_acknowledge`) wait for the page before the next one starts. Results print to
the terminal.
"""

import asyncio
import json
import os
import subprocess
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

REPO = Path(__file__).resolve().parent

app = typer.Typer(add_completion=False)
console = Console()


@contextmanager
def status_spinner(text: str) -> Iterator[Console]:
    with console.status(f"[cyan]{text}[/cyan]"):
        yield console
    console.print(f"[green]✓[/green] {text}")


def _server(accent: str | None) -> StdioServerParameters:
    # The client passes only a handful of variables by default; the server needs
    # DISPLAY/WAYLAND_DISPLAY and any TEXT_WALL_BREAKER_* override to open a page. This
    # script's own VIRTUAL_ENV would make `uv run` warn about the repository's .venv.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if accent:
        env["TEXT_WALL_BREAKER_ACCENT"] = accent
    return StdioServerParameters(
        command="uv",
        args=[
            "run",
            "--directory",
            str(REPO),
            "text-wall-breaker-mcp",
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
    # an image in the question and free text.
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

    # Several questions as a wizard, with header, ids and prefill.
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


_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "9c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def _git(repo: Path, *args: str) -> None:
    # Signing is off: a scratch repo has no use for the operator's key.
    subprocess.run(  # noqa: S603
        [
            *["git", "-C", str(repo), "-c", "user.name=showcase", "-c", "user.email=s@example.com"],
            *["-c", "commit.gpgsign=false", *args],
        ],
        check=True,
        capture_output=True,
    )


GUIDE_OLD = (
    ["# Deploy guide", "", "Deploy from `main` only.", "", "## Steps", ""]
    + [f"{i}. Step {i}." for i in range(1, 6)]
    + ["", "## Rollback", "", "Redeploy the previous tag.", ""]
    + [f"Filler paragraph {i}.\n" for i in range(1, 16)]
    + ["| Env | Approval |", "| --- | --- |", "| prod | one reviewer |", "", "Done."]
)
GUIDE_NEW = list(GUIDE_OLD)
GUIDE_NEW[2] = "Deploy from `main` only. **Never on a Friday.** ![x](pic.png)"
GUIDE_NEW[14] = "Redeploy the previous tag and open an incident."
GUIDE_NEW[-3] = "| prod | two reviewers |"

SERVICE_OLD = ["import os", "", "RETRIES = 3", ""] + [
    f"def step_{i}():\n    return {i}\n" for i in range(1, 25)
]
SERVICE_NEW = list(SERVICE_OLD)
SERVICE_NEW[2] = "RETRIES = 5"
SERVICE_NEW[-1] = "def step_24():\n    return 24 + RETRIES\n"

ADOC_OLD = "= Architecture\n\nThe service has one queue.\n\n== Parts\n\n* queue\n* worker\n"
ADOC_NEW = (
    "= Architecture\n\nThe service has one queue and a dead-letter queue.\n\n== Parts\n\n"
    "* queue\n* worker\n* dead-letter queue\n\n[source,python]\n----\nqueue.put(item)\n----\n"
)


def _demo_repo(root: Path) -> Path:
    """A scratch repo whose last commit touches Markdown, AsciiDoc and Python files."""
    repo = root / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "src").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    (repo / "docs" / "guide.md").write_text("\n".join(GUIDE_OLD) + "\n")
    (repo / "docs" / "arch.adoc").write_text(ADOC_OLD)
    (repo / "src" / "service.py").write_text("\n".join(SERVICE_OLD) + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "docs" / "guide.md").write_text("\n".join(GUIDE_NEW) + "\n")
    (repo / "docs" / "pic.png").write_bytes(_PNG)
    (repo / "docs" / "arch.adoc").write_text(ADOC_NEW)
    (repo / "docs" / "new-page.md").write_text("# New page\n\nAll of it is added.\n")
    (repo / "src" / "service.py").write_text("\n".join(SERVICE_NEW) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo


async def showcase_review(client: Client, tmp: Path) -> None:
    await _call(
        client,
        "pages: header, blurb, agent notes (one on a deleted line, one on a run)",
        "review",
        {
            "header": (
                "# Reviewing a sample change\n\nClick a line (shift-click for a run, or select "
                "text) to comment; Reply on an agent note answers it.\n\n- the heading and chips "
                "stay in the top row; this part folds under *Details*"
            ),
            "title": "Showcase: review (pages)",
            "pages": [
                {"path": "src/app/config.py", "diff": DIFF_ASK, "blurb": "Raised the **timeout**."},
                {"path": "README.md", "diff": DIFF_PAGE},
            ],
            "notes": [
                {
                    "path": "src/app/config.py",
                    "side": "old",
                    "line": 3,
                    "text": "The old value was too tight for the **batch** endpoint.",
                },
                {
                    "path": "src/app/config.py",
                    "side": "new",
                    "line": 3,
                    "end_side": "new",
                    "end_line": 4,
                    "text": "New default and a retry budget.\n\n```python\nTIMEOUT = 60\n```",
                },
            ],
        },
    )
    repo = _demo_repo(tmp)
    await _call(
        client,
        "repo mode: Expand context, rendered view (Markdown, AsciiDoc), a new file, notes",
        "review",
        {
            "repo": str(repo),
            "git_range": "HEAD~1..HEAD",
            "context_lines": 3,
            "header": (
                "# Repo mode\n\nThe server ran `git diff HEAD~1..HEAD` itself. Show the hidden "
                "lines between hunks with the bars; click a block of a rendered file to comment; "
                "`new-page.md` opens on its rendered view.\n\n"
                "AsciiDoc renders only with `ruby` and the `asciidoctor` gem installed."
            ),
            "title": "Showcase: review (repo)",
            "notes": [
                {
                    "path": "docs/guide.md",
                    "side": "new",
                    "line": 3,
                    "text": "The Friday rule comes from the **incident review**.",
                },
                {
                    "path": "src/service.py",
                    "side": "new",
                    "line": 3,
                    "text": "Five tries: the third failed twice in the load test.",
                },
            ],
        },
    )


async def _run(tools: list[str], accent: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="text-wall-breaker-showcase-") as tmp:
        images = _images(Path(tmp))
        rprint("[cyan]Starting text-wall-breaker server from this checkout")
        async with Client(_server(accent)) as client:
            if "ask" in tools:
                await showcase_ask(client, images)
            if "show" in tools:
                await showcase_show(client)
            if "review" in tools:
                await showcase_review(client, Path(tmp))


@app.command()
def run(
    tool: Annotated[
        list[str] | None, typer.Argument(help="ask, show, review; all when omitted.")
    ] = None,
    accent: Annotated[
        str | None, typer.Option(help="TEXT_WALL_BREAKER_ACCENT for the server, e.g. '#2563eb'.")
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

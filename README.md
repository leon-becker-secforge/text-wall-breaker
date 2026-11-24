# text-wall-breaker

An MCP server that asks the user a question, shows them a result, or has them review a
diff, in a browser page.

A wall of unformatted text is hard to read, and HTML is the tool for fixing that. But
hand-writing a page per question doesn't scale, and a model generating HTML inline tends
to look it — inconsistent, slow to produce, obviously AI-written. `text-wall-breaker` covers
the common cases — options with explanations, images, long text — with one well-designed
page instead, so neither cost has to be paid per question.

Claude Code's built-in AskUserQuestion renders inside the terminal panel: at most four
options, plain-text previews, no images. `text-wall-breaker` opens a real page on the desktop
instead, so a question can carry any number of options, a Markdown body per option, and
images.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/ask-dark.png">
  <img alt="An ask page with four options, the recommended one expanded" src="docs/images/ask-light.png" width="720">
</picture>

## Tools

The server provides five tools. The table gives what each waits for and what it is for.
The agent calls `live` in a loop during a live session that a `review` or an `ask`
started.

| Tool | Waits for | Use it for |
| --- | --- | --- |
| [`ask`](docs/ask-tool.md) | the user's answer | a question with options, free text, rankings or several steps |
| [`show`](docs/show-tool.md) (prototype) | nothing, or an Acknowledge click | a report or comparison the user only reads |
| [`show_status`](docs/show-tool.md#show_status) | nothing | checking whether the last `show` was acknowledged |
| [`review`](docs/review-tool.md) | the user's comments | line-anchored comments on a diff |
| [`live`](docs/live-tool.md) | the user's next message | a live session that a `review` or an `ask` started |

Three more documents cover what applies to several tools and to the project:

- [Markdown subset](docs/markdown-subset.md): what a page renders and how images and links resolve.
- [How a call works](docs/how-a-call-works.md): the loopback listener, the shared tab and timeouts.
- [Developing](docs/developing.md): running the tests and the checks before going public.

## Installing

Both ways below run it with [uv](https://docs.astral.sh/uv/), which also fetches a fitting
Python: 3.13.6 or newer, since older releases have CVE-2025-6069 (a slowdown in the HTML
parser that renders `review`'s AsciiDoc view). A checkout runs on the version in
`.python-version`, the newest stable one. `review` with `repo` needs git 2.40 or newer.

Register it with Claude Code, from a checkout of this repository:

```bash
claude mcp add text-wall-breaker -- uv run --directory /path/to/text-wall-breaker text-wall-breaker-mcp
```

Or straight from the Git repository, with no checkout (a tag pins a release):

```bash
claude mcp add text-wall-breaker \
  -- uvx --from git+ssh://git@github.com/leon-becker-secforge/text-wall-breaker.git@v0.23.0 text-wall-breaker-mcp
```

It is not published to PyPI: the name `text-wall-breaker` is not reserved there for this
project.

The server refuses to start on a host it cannot show a page on, and exits with status 2
and a message on stderr instead. That surfaces as a connection error in the client
rather than as a failure inside the first question asked. What it opens pages with, and
so what it checks for at launch, depends on the platform:

| Platform | Opener | Refuses to start when |
| --- | --- | --- |
| Linux desktop | `webbrowser`, then `xdg-open` | `xdg-open` is missing, or neither `DISPLAY` nor `WAYLAND_DISPLAY` is set |
| WSL | `wslview` if installed, else `explorer.exe` | `wslview` is missing, and `explorer.exe` is neither on `PATH` nor at `/mnt/c/Windows/explorer.exe` (Windows interop off) |
| macOS | `open` | `open` is missing |
| Windows | `os.startfile` | never |

Under WSL, pages open in the Windows browser, not a Linux one inside WSLg, and no
display variable is needed. With the `explorer.exe` fallback, a one-shot page's file is
first translated to its `\\wsl.localhost\...` path with `wslpath -w`; explorer.exe's exit
status is ignored, since it reports 1 even on success, so a page that never opened
shows up as `not_delivered` instead. Any other status, or any output on stderr, is
written to the server's stderr, which the client keeps in its MCP log. With
`appendWindowsPath = false` in `/etc/wsl.conf` the Windows directories are not on `PATH`
although interop works; the server then looks for `/mnt/c/Windows/explorer.exe` itself.
`file://` images on such a one-shot page still
point at Linux paths the Windows browser can't read; pages on the shared tab load
images over the loopback listener and are unaffected.

## Options

Options are command-line arguments to `text-wall-breaker-mcp`, placed after it in the
registration. All are optional. An unknown option, or a value that can't be used, makes
the server refuse to start with status 2 and a message on stderr, the same way the host
checks above do. `text-wall-breaker-mcp --help` lists them.

| Option | Effect | Default |
| --- | --- | --- |
| `--opener COMMAND` | Open pages with `COMMAND` instead of the platform's opener. See [Opener override](#opener-override). | the table above |
| `--accent #RRGGBB` | The pages' accent colour. See [Accent colour](#accent-colour). | green |
| `--log-level LEVEL` | `debug`, `info`, `warning`, `error`, `critical`, or `off` for no log file. See [Log file](#log-file). | `info` |
| `--no-glossary` | Don't define the project's glossary terms on `ask` pages. See [Glossary hints](docs/ask-tool.md#glossary-hints). | glossary on |

With `claude mcp add`, everything after `--` is the server's command line:

```bash
claude mcp add text-wall-breaker -- uv run --directory /path/to/text-wall-breaker text-wall-breaker-mcp --accent '#2563eb' --no-glossary
```

In an `.mcp.json`, the options go in `args`:

```json
{
  "mcpServers": {
    "text-wall-breaker": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/text-wall-breaker", "text-wall-breaker-mcp",
        "--opener", "firefox --new-tab",
        "--accent", "#2563eb"
      ]
    }
  }
}
```

### Opener override

`--opener` takes a command line to open pages with, for a setup the table above gets
wrong. The page's path or URL is appended as the last argument. The command is split
shell-style but not run through a shell, so it is one argument: quote it on a shell
command line, and give it as a single string in `args`. An override also skips the
display check. At launch, the first word must resolve on `PATH` (or be a path to an
executable), otherwise the server refuses to start.

An override gets a one-shot page (used when the shared tab is busy) as a plain file path,
without the `wslpath -w` translation the built-in WSL opener applies. Under WSL, an override
that hands the target to a Windows program therefore opens such a page only if the program
understands Linux paths. Pages on the shared tab are loopback URLs and are unaffected.

```bash
claude mcp add text-wall-breaker -- uv run --directory /path/to/text-wall-breaker text-wall-breaker-mcp --opener "firefox --new-tab"
```

### Accent colour

The page's accent (the selected-option border and tint, the submit/Acknowledge button,
focus rings) defaults to green, for `ask`, `show`, and `review` alike. `--accent` takes a
`#rrggbb` hex colour to use instead, in both light and dark theme. Quote it on a shell
command line, where an unquoted `#` can start a comment:

```bash
claude mcp add text-wall-breaker -- uv run --directory /path/to/text-wall-breaker text-wall-breaker-mcp --accent '#2563eb'
```

The text colour on top of the accent (e.g. the submit button's label) and the tint
behind a selected option are derived from the colour given, not configured separately.
A value that isn't a hex colour makes the server refuse to start, rather than open
pages that silently ignore it.

### Log file

The server writes `text-wall-breaker.log` under `$XDG_STATE_HOME/text-wall-breaker/`
(default `~/.local/state/text-wall-breaker/`), readable only by the user. It records
the opener's command and exit status, each page's delivery and each timeout, and a
`not_delivered` result names it. It is rotated at server start once past 1 MB, keeping two backups.
`--log-level` sets how much it records; `--log-level off` keeps no log.

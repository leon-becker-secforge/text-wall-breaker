# pretty-prompt

An MCP server that asks the user a question, shows them a result, or has them review a
diff, in a browser page.

A wall of unformatted text is hard to read, and HTML is the tool for fixing that. But
hand-writing a page per question doesn't scale, and a model generating HTML inline tends
to look it — inconsistent, slow to produce, obviously AI-written. `pretty-prompt` covers
the common cases — options with explanations, images, long text — with one well-designed
page instead, so neither cost has to be paid per question.

Claude Code's built-in AskUserQuestion renders inside the terminal panel: at most four
options, plain-text previews, no images. `pretty-prompt` opens a real page on the desktop
instead, so a question can carry any number of options, a Markdown body per option, and
images.

The server exposes four tools: `ask`, which waits for an answer; `show` (prototype),
which doesn't — it opens a page for a result the user only needs to read, like a report
or a comparison, and returns as soon as the browser has it; and `review`, which waits for
line-anchored comments on a diff instead of a discrete answer; and `show_status`, which
reports without blocking whether the last `show` was acknowledged.

## Installing

Register it with Claude Code, from a checkout of this repository:

```bash
claude mcp add pretty-prompt -- uv run --directory /path/to/python-libs --package pretty-prompt pretty-prompt-mcp
```

Or from the released wheel, with no checkout:

```bash
claude mcp add pretty-prompt \
  --env UV_INDEX_INTERNAL_USERNAME=<user> --env UV_INDEX_INTERNAL_PASSWORD=... \
  -- uvx --index internal=https://index.example.com/simple/ --from pretty-prompt pretty-prompt-mcp
```

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

### Opener override

Set `PRETTY_PROMPT_OPENER` to a command line to open pages with that instead, for a
setup the table above gets wrong. The page's path or URL is appended as the last
argument; the command is split shell-style but not run through a shell. An override
also skips the display check. At launch, the first word must resolve on `PATH` (or be
a path to an executable), otherwise the server refuses to start:

An override gets a one-shot page (used when the shared tab is busy) as a plain file path,
without the `wslpath -w` translation the built-in WSL opener applies. Under WSL, an override
that hands the target to a Windows program therefore opens such a page only if the program
understands Linux paths. Pages on the shared tab are loopback URLs and are unaffected.

```bash
claude mcp add pretty-prompt --env PRETTY_PROMPT_OPENER="firefox --new-tab" -- uv run --directory /path/to/python-libs --package pretty-prompt pretty-prompt-mcp
```

### Accent colour

The page's accent — the selected-option border and tint, the submit/Acknowledge button,
focus rings — defaults to green, for `ask`, `show`, and `review` alike. Set
`PRETTY_PROMPT_ACCENT` to a `#rrggbb` hex colour to use something else instead, in both
light and dark theme:

```bash
claude mcp add pretty-prompt --env PRETTY_PROMPT_ACCENT=#2563eb -- uv run --directory /path/to/python-libs --package pretty-prompt pretty-prompt-mcp
```

The text colour on top of the accent (e.g. the submit button's label) and the tint
behind a selected option are derived from the colour given, not configured separately.
An unset or invalid value falls back to the default green; an invalid one also makes
the server refuse to start, for the same reason as the opener checks
above — surfaced once at launch rather than as a page that silently ignored it.

## The `ask` tool

```json
{
  "question": "Which backend?\n\n![diagram](file:///tmp/arch.png)",
  "options": [
    {"id": "pg", "label": "PostgreSQL", "caveat": "Needs the `pg` operator installed.",
     "body": "Ships with the **operator**, so no extra service."},
    {"id": "sqlite", "label": "SQLite"}
  ],
  "multi_select": false,
  "allow_free_text": true
}
```

`question` and each option's `body` are Markdown. `label` is short plain text and is not
rendered as Markdown. `id` is what comes back in the answer, so an answer stays stable
when a label is reworded.

`options` may be empty only when `allow_free_text` is true; otherwise the page would
have no way to answer and the call is rejected before anything opens.

Selecting an option and writing free text are additive, never exclusive. The send button
enables once at least one option is selected **or** the free-text box is non-empty, and
an answer may carry both.

An option's body over ~400 characters, or one carrying an image or a fenced code block,
collapses behind a click-to-open row instead of always being shown. `recommended: true`
marks the option you lean towards with a Recommended badge and pre-expands its row if it
has one, without pre-selecting it. At most one option per question may be recommended
unless `multi_select` is set. Two or more image-bearing options switch the whole page to a widened, grid gallery
instead (comparing screenshots side by side beats one at a time behind a drawer), and
never collapse behind one. Every image, gallery or not, is click-to-zoom: it expands
full-size in a lightbox, dismissed by clicking again or Escape.

An option's `caveat` is one line of inline Markdown (code, bold, links; at most 160
characters) that is always visible under the label, as a tinted strip, even while a long
body is collapsed. It is for whatever could flip the choice: a precondition, a cost, a
conflict with an earlier answer. Users often pick an option without opening its body, so
that sentence must not live there. Most options need no caveat; a longer or multi-line
caveat is rejected before anything opens.

`header` (prototype, `questions` only) is Markdown shown above the progress bar, so on
every step: typically what earlier rounds already settled, so each answer is given with
it in view. A long header collapses behind a "Context" toggle, which also says how many
items it holds when it is one list. Setting `header` on a single question is rejected;
put the context in `question` instead.

The `live` parameter opens the ask as a live session right away; the user can also start
one from an option. The session is described in [Live ask](#live-ask).

A question longer than 400 characters (`WIDE_QUESTION_CHARS`) widens the page to the
wide column. The limit is soft: nothing is rejected.

### Several questions on one page

`header` is Markdown above every step. On a single `question` it is appended to the
question as a further paragraph.

`questions` (prototype) replaces `question`/`options`/`multi_select`/`allow_free_text`
(set one or the other, never both) to ask several questions on a single page, presented
as a wizard: one question per screen, a progress bar, Back/Next.

```json
{
  "questions": [
    {"question": "Which backend?", "options": [{"id": "pg", "label": "PostgreSQL"}]},
    {"question": "Which region?", "allow_free_text": true}
  ]
}
```

Each entry takes the same keys `question`/`options`/`multi_select`/`allow_free_text`
takes for one question. The result carries one answer per question, in order, under
`answers` instead of at the top level; see Result below.

### Glossary hints

If the server's working directory, or a folder above it up to the git root, holds a
`GLOSSARY.md` (else a `CONTEXT.md`), the page underlines the first occurrence of each
term per question and defines it in a popover on hover or keyboard focus. The nearest
file wins, so a library folder's own glossary beats the repo root's. Terms are matched as
whole words, case-insensitive, outside code. The file format is the one the
`domain-modeling` skill writes: `**Term**:`, then the definition.

`ask` takes `project`, a folder to look in instead of the working directory, for a git
worktree or a session started elsewhere. Set `PRETTY_PROMPT_GLOSSARY=off` to turn the
hints off.

### Result

Exactly one of:

```json
{"selected": ["pg"], "free_text": "", "expanded": []}
{"answers": [{"selected": ["pg"], "free_text": "", "expanded": []}, ...]}
{"cancelled": true}
{"abandoned": true}
{"not_delivered": true, "reason": "..."}
```

The first shape answers `question`; the second, one entry per question in order,
answers `questions`. Cancel is a control of its own on the page, so an answer the user
declined to give is distinguishable from one that never arrived.

There is no deadline for an answer. An open page sends a heartbeat every 15 seconds,
hidden or not, and the call waits as long as it keeps coming. `abandoned` means the
page went silent for 3 minutes: the tab was closed, the browser quit or the machine
slept. Browsers slow a background tab's timers to about once a minute, which is why the
window is that long. An abandoned `ask` returns `resume` like a cancelled one, when
answers were entered.

### Exporting an answer by hand

Every `ask` and `review` page has a download icon beside the theme toggle. It opens a
panel with the answer entered so far as the same JSON the page shows when Claude Code
stops waiting, and a Copy button. The user can paste that to Claude Code at any time,
for example before a long wait could end the call. The page stays open and usable, and
nothing is sent. The Copy button writes to the clipboard on the user's click; the page
never copies anything on its own. `show` has no icon: the user enters nothing there.

### Long waits and background tasks

Claude Code moves an MCP call still running after 120 s to a background task. The
answer then arrives as a task notification marked "NOT USER INPUT", which an agent must
not treat as the user's answer or approval, so it asks the user to repeat it in chat.
The limit belongs to Claude Code, not to pretty-prompt. Per the
[Claude Code MCP docs](https://code.claude.com/docs/en/mcp.md):

- `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` sets the threshold (default `120000`) for all
  servers; `0` disables backgrounding. Set it in the environment of `claude`, or under
  `env` in `settings.json`.
- Calls from subagents, in non-interactive mode, and while an MCP elicitation dialog is
  open are never backgrounded.
- `MCP_TOOL_TIMEOUT` (wall clock) and the `.mcp.json` `timeout` field (per server) are
  separate. `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` defaults to 30 minutes for stdio servers.
- Not documented: the notification's exact wording, and elicitation in print mode or
  the Agent SDK.

Claude Code also aborts a call whose server sends no response and no progress
notification for an idle window: 30 minutes for a stdio server like this one
(`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, in milliseconds). While a call waits on the user,
pretty-prompt therefore sends a progress notification every 60 seconds, so the idle
window never ends a call to `ask`, `review` or `show` with `wait_for_acknowledge`, even
when the page stays open for hours. A progress notification does not extend a
wall-clock limit: `MCP_TOOL_TIMEOUT` (default about 28 hours) or a per-server `timeout`
in `.mcp.json` ends the call when its time elapses.

`not_delivered` means the page never loaded in a browser, so the user never saw the
question. It comes back within about 30 seconds of opening the tab, so the agent can
fall back to asking in the terminal. A page that loaded and was then closed ends in
`abandoned`.

`expanded` is the ids of options whose collapsed body was opened at any point before the
answer was sent. It stays set even if the drawer was closed again afterwards, so an id
can be expanded without being selected, or selected without ever being expanded (a short
option, or one never opened).

`changed_from` is the ids that were picked at some point but are not in `selected`, in
the order they were first picked: what the user considered and gave up. In the
`questions` shape each entry also carries `visits`, how often that question was shown;
1 means the user never came back to it, more means they returned via Back.

```json
{"answers": [{"selected": ["b"], "free_text": "", "expanded": [], "changed_from": ["a"], "visits": 2}]}
```

> [!IMPORTANT]
> `expanded`, `changed_from` and `visits` record how the user reached an answer, not just
> the answer, and all three are sent to the agent. Tell the people answering. To stop
> sending `changed_from` and `visits`, set `SHARE_INTERACTION_SIGNALS = False` in
> `src/pretty_prompt/ask.py`; the tool description then stops mentioning them too.

## The `show` tool (prototype)

```json
{
  "content": "# Review findings\n\n| file | severity |\n|---|---|\n| ask.py | high |",
  "title": "Review: auth middleware",
  "save_button": true,
  "acknowledge_button": true,
  "wait_for_acknowledge": false
}
```

`content` is Markdown, rendered the same way `ask`'s `question` is. Unlike `ask`, `show`
doesn't wait for anything by default — it opens the page and returns as soon as the
browser has it, so it's the tool to reach for when a result only needs to be read, not
answered: a report, a comparison, a diff, a table too long for a terminal reply.

The page carries two buttons, on by default, either can be turned off:

- **Save report** — downloads the content, as the original Markdown or as a static
  snapshot of the rendered page (no buttons, no scripts). Purely client-side; never
  reported back here.
- **Acknowledge** — marks the report seen. Set `wait_for_acknowledge` to block on it,
  the way `ask` blocks on an answer. Otherwise the click is remembered and readable
  through `show_status` (below).

### An unread report is not replaced

`show` shares its tab with `ask` and `review`. While a report is unacknowledged and its
tab is still open, any later call (`ask`, `review` or another `show`) opens a second
tab instead of taking the first over, so the report stays on screen. Once it is
acknowledged, or its tab is closed, the next call reuses the tab again and the report is
gone. When a question about the report follows, set `wait_for_acknowledge`, or
check `show_status` first.

### `show_status`

Takes no arguments; returns at once with `{"state": "unacknowledged" | "acknowledged" |
"none", "title": "..."}`. `none` means no report was shown in the shared tab, or
something else replaced it. A report that fell back to its own one-off tab (the shared
one was busy, or held an unread report) has no listener, so it is not tracked: the
result keeps describing the shared tab's report, or reads `none`. `title` tells them
apart.

### Result

```json
{"opened": true, "path": "..."}
{"acknowledged": true}
{"abandoned": true}
{"not_delivered": true}
```

Without `wait_for_acknowledge`, always `{"opened": true}`, plus `path` when the call
didn't reuse an already-open tab (see below). With it, `{"acknowledged": true}` once
Acknowledge is clicked, or `{"abandoned": true}` if the tab is closed first. Either
way, `{"not_delivered": true}` means the page never loaded (see `ask`'s Result). A
one-off page without `wait_for_acknowledge` has no listener to tell, so it always
reports `opened`.

## The `review` tool

```json
{
  "pages": [
    {"path": "src/foo.py", "diff": "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,3 +1,3 @@\n def foo():\n-    return 1\n+    return 2\n",
     "blurb": "Why this file changed."}
  ],
  "header": "Reviewing PR #42 — focus on the error-handling path."
}
```

Unlike `ask` and `show`, a `review` call's reaction doesn't reduce to a discrete choice
or a plain acknowledgement — it attaches to specific lines of a diff. It blocks the same
way `ask` does, with no deadline: it waits for the user to submit or cancel, or for the
tab to be closed.

Each entry of `pages` is one file's diff, raw unified diff text (e.g. straight `git diff`
output) — a `@@ -a,b +c,d @@` hunk header is required so a line's coordinate can be
resolved; a diff with none is rejected before anything opens. `blurb` is optional
Markdown shown at the top of that page only, e.g. why that file changed. `header` is one
more Markdown block, shown above every page.

When a deleted line is followed by a replacement that shares at least half of its tokens,
the page marks the words that differ on both lines, in square boxes darker than the row
(`mark.pp-chg`). A changed word is marked whole, and changes separated only by spaces merge
into one box. Lines that look nothing alike, and lines over 2000 characters, get no marks.

For a change in git, pass `repo` instead of `pages` (never both): the server runs
`git diff` itself and makes one page per changed text file, so the diff is not read into
the agent's context and pasted back. `git_range` is what `git diff` takes (`main..HEAD`,
`abc123^!`; default `HEAD`, the uncommitted changes to tracked files), `paths` limits it,
`context_lines` is `-U` (default 3). Binary files and pure mode changes have no line to
comment on and are skipped. A renamed file is one page under its new path (a rename
without edits shows a single hunk line saying so), and comments and `notes` use that path.

Each file carries a status letter in the file list and above its diff: `A` added (green),
`M` modified (amber), `D` deleted (red), `R` renamed (gray; its tooltip and the list filter
know the old path). For `repo` it comes from git's header lines; for `pages` from the diff
text alone: its `diff --git` header lines when it has them, else its `---`/`+++` lines, else
the first hunk (a file that starts at line 0 is new, one that ends at line 0 is deleted).

A `repo` page also carries the file's text, so the user can show the lines the diff leaves
out. Each hidden stretch (before the first hunk, between hunks, after the last) is a dashed
bar with a pill: the middle shows the whole stretch, and `↓ 10` / `↑ 10` show ten lines
below the hunk above or above the hunk below (touch screens show the steps always, others
on hover or focus). Revealed lines are ordinary rows: they can be selected and commented,
with the old and new numbers a diff would give them. A selection cannot span a stretch that
is still hidden; the dock says so and offers no Comment until those lines are shown. The
file comes from the working tree when the diff is against it, and from the end of the range
(`B` in `A..B`, `X` in `X^!`) otherwise. It is left out, and that file shows only its
hunks, when it is over 512 KB, past 4 MB over all files, binary or not UTF-8, a symlink,
or not the text the diff was made from. `pages` has no file to read, so it never expands.

A `repo` page for a `.md`, `.markdown`, `.adoc` or `.asciidoc` file also gets a rendered
view of its new side. The page is cut into chunks, one per rendered block: the source rows
of the block on the left (every line of the file, with its diff marks) and the block
rendered on the right. Below 1000px wide a chunk stacks, rows above block. Blocks the diff
does not touch fold into a "N unchanged blocks" bar, except those next to a changed one
and those with an agent note; the bar opens them. Clicking a block (shift-click extends
over several) selects the source lines it stands for, lights them up in the diff, and
opens the comment box; the result is the usual range comment on new-side lines, so
`context` is the source text. Dragging over text in the rendered view selects the blocks it touches and
records the text as the comment's `quote`, marked in the diff row as well. The quote is
kept only if it reads the same in the source; a selection across formatting (`**bold**`,
a link) comments on the blocks with no quote.

- Markdown uses `markdown-it-py`; raw HTML in the file is escaped. A block is a heading,
  paragraph, list item, quote, table or table row, or code block.
- AsciiDoc needs `ruby` and the `asciidoctor` gem on the machine; without them the file
  shows the diff only, with no error. It runs in secure mode (no `include::`). A block is
  a heading, paragraph, list, list item, table, listing and so on; a table is one block.
- Links open in a new tab. A remote image is not loaded (the alt text shows instead), and
  a local one only if it is a file inside the repository.
- A page from `pages` has no file text, so its rendered view covers only the lines the
  diff shows. The rendered view leaves out the lines between the hunks, so a list or code
  block that starts before a hunk can render differently than in the file.

With more than one page, a file list stays in view on the left: one row per file with its
directory, the current file marked, a comment count, and a pencil where the file has a
note. A click, the arrow keys or `j`/`k` switch files. Below 900px wide the list becomes a
drawer opened from a bar at the top, which also shows the current file and Prev/Next, so
the diff keeps the full width. With exactly one page, that chrome is skipped entirely
rather than shown inert.

A comment covers a line or a run of lines. Clicking a diff line selects it, shift-clicking
extends the selection from the last click, and selecting text with the mouse takes the
rows it touches (Enter or Space on a focused row selects it too, shift extends). The rest
of the diff dims and a box opens under the last selected row for the comment; Ctrl+Enter
adds it and Esc clears the selection. A comment shows under its rows with Edit and Delete
until the review is sent. A run may start on a deleted row and end on an added one.
Selecting text inside the rows also records it as the comment's `quote`.

The agent can explain itself with `notes`, each `{path, side, line, text}` plus optional
`end_side`/`end_line` for a run, `text` in Markdown. A note opens under its last row, with
a "✦ note" chip on its first row to fold it and a bar above the diff to hide or show all
of a page's notes. Reply selects the note's rows and opens the comment box. Notes are
read-only and not returned. They must sit on rows the page shows: a changed line or its
context, and an old-side line only if deleted; anything else is a tool error. Hidden lines
revealed by expansion cannot hold a note.

Each page has a "Note on this file" box below its diff for a remark about the whole file,
and an "Overall note" box below the pages holds one for the whole call; neither is tied to
a line. Once Submit or Cancel is sent, nothing accepts further input.

### The `pp-review` prompt

The server offers one MCP prompt, `pp-review`. It asks the agent to find the current
branch's base, read the diff, call `review` with `repo` and a `git_range` of
`<merge-base>..HEAD`, and add `notes` on the most important changes. The optional `base`
argument names the base; without it the agent uses the branch's upstream, else the default
branch. Claude Code lists an MCP prompt as `/mcp__pretty-prompt__pp-review`, with `base` as
the argument.

### Result

```json
{"comments": [{"path": "src/foo.py", "side": "new", "line": 2, "text": "why 2?", "context": "    return 2"}],
 "page_notes": [{"path": "src/foo.py", "text": "Split this file."}],
 "overall_note": "Looks fine overall."}
{"cancelled": true, "comments": [...], "page_notes": [...], "overall_note": null}
{"abandoned": true, "comments": [...], "page_notes": [...], "overall_note": null}
{"not_delivered": true}
```

Submit always succeeds — an empty review (no comments, no notes) is itself a valid
answer, since there's nothing to validate. `page_notes` lists only the files the user
wrote a note for, each as `{"path", "text"}`, and is `[]` otherwise. Each comment is
resolved server-side from the diff you gave, not trusted from the page: `side` is `"old"`
or `"new"`, `line` is that side's 1-based line number in the file at `path` (not its
position in the diff text), and `context` is that line's own source text, so the agent
doesn't have to re-parse its diff to see what a comment was about.

A comment on a run of lines has the same fields plus `end_side` and `end_line`, the last
row of the run, and each end names its own side (`side: "old", end_side: "new"` is a run
from a deleted row to an added one; see [ADR 0003](docs/adr/0003-comment-ranges-carry-a-side-per-end.md)).
Its `context` is every row of the run, one per line, each prefixed with `+`, `-` or a
space, so a mixed run is unambiguous. A comment made by selecting text adds `quote`, the
selected part, only when it is shorter than the rows. A single-line comment is unchanged
since 0.9.0:

```json
{"path": "src/foo.py", "side": "new", "line": 2, "end_side": "new", "end_line": 3,
 "text": "why both?", "context": "+    return 2\n+    # twice", "quote": "return 2\n    # tw"}
```

Cancel returns the same shape as Submit, plus `"cancelled": true` — whatever comments and
overall note already existed are kept, not discarded, since the user may cancel
deliberately after a partial pass, intending the agent to act on what's already flagged.
This is the one deliberate difference from `ask`'s all-or-nothing cancel.

Abandoned (the tab was closed) returns the same shape plus `"abandoned": true`. The
page reports its comments with every heartbeat and shortly after typing, so the result
holds what was entered up to the last report, not an empty review.

### Live session (`live`)

A live session is a `review` or an `ask` the user and the agent work through together. In
a review, the user asks about lines or requests changes while still reviewing, and the
agent answers and shows the changed diff on the same page (see [ADR 0004](docs/adr/0004-live-review-is-a-multi-call-session-with-one-result.md)).
It needs the shared browser tab to start, so a `review` that had to open its own tab
cannot start one. Once started, the session owns its tab and leaves the shared one free,
so other calls open their own tab and several live sessions can run at once (see
[ADR 0005](docs/adr/0005-a-live-session-owns-its-tab-and-ask-has-them-too.md)). `ask` has
live sessions too, described in [Live ask](#live-ask).

A session starts in one of two ways:

- The user presses "Start live session" in the card above the file list. The blocking
  `review` call then returns `{"live": {"session": "ls-..."}, "instruction": "..."}`.
- The agent passes `live: true` to `review`, which returns the same as soon as the page
  has loaded.

`session` is the id that every `live` call of the session passes. `instruction` tells the
agent to start calling `live`. The `review_live` tool of earlier versions is now `live`,
without an alias.

The agent then calls `live` in a loop. Each call posts the agent's `replies` and an
optional refresh, then blocks until the user sends something:

```json
{"session": "ls-1a2b3c4d", "replies": [{"to": "t1", "text": "Fixed.", "done": true}],
 "refresh": true}
```

In this example, `t1` is the thread the reply goes to (`"chat"` addresses the chat),
`done` asks the user to resolve the thread, and `refresh` diffs the repository again. A
session opened with `pages` sends replacement `pages` instead. `wait: false` posts and
returns at once, for acknowledging a request before long work.

A call returns what the user sent since the last one:

```json
{"messages": [
  {"thread": "t2", "path": "src/foo.py", "side": "new", "line": 2, "text": "why 2?", "context": "    return 2"},
  {"thread": "t1", "text": "Not quite."},
  {"chat": "Also rename foo to bar."},
  {"resolved": "t1"}]}
```

In this example, `t2` is a new thread with the same fields as a `review` comment. `t1`
gets a further message from the user. The `chat` entry is a request about the whole
change. `resolved` means the user resolved `t1`.

A thread is a comment the user sent to the agent with "Ask the agent now" switched on in
the comment box; other comments are not sent to the agent and only reach the report.

On a refresh, the page reloads with the new diff. A comment moves with its lines; a
comment whose lines are gone is shown folded as outdated. A refresh that arrives while
the user's comment box is open waits until the box closes.

Only the user ends a session. The call that receives the end returns the `review` result
(`cancelled` and `abandoned` as above), plus `threads`, one
`{"id", "path", "side", "line", "resolved", "last"}` per thread. Outdated comments come
back with `"outdated": true` and their old `context`. A refresh sent after the end is not
applied and the result contains `"refresh_dropped": true`.

### Live ask

A live `ask` lets the user question the agent before answering, with the page kept open.
Each option has a thought bubble, shown while the pointer is over the option or the option
has focus (always on a touch screen). A question without options has one beside the
question. A press is the question: it opens a side panel with one chat and sends "Tell me
about this option." (or "...this question.") at once, so the agent answers without the user
typing. What the user types afterwards is a follow-up. The user still sends the answer with
the page's own buttons.

A session starts in one of two ways, as for a review:

- The user presses a bubble. The blocking `ask` call then returns
  `{"live": {"session": "la-..."}, "instruction": "..."}`.
- The agent passes `live: true` to `ask`, which returns the same as soon as the page has
  loaded.

The agent then calls `live` in a loop, with `replies` addressed to `"chat"`. A call
returns what the user sent since the last one:

```json
{"messages": [{"chat": "Does B survive a crash?", "option": "b"}]}
```

In this example, `chat` is the user's message and `option` is the option it is about.
`option` is absent when the user writes to the chat without naming one. With `questions`, a message about an option also
has `question`: the question's `id`, or its position from 0 when it has none.

Only the user ends a session, by sending the answer, cancelling or closing the tab. The
call that receives the end returns the `ask` result (`cancelled` and `abandoned` as
above), plus `chat`: every message of the session, each
`{"from": "operator" | "agent", "text"}`. A call that had to open its own tab cannot be
live: its page has no bubble, and `live: true` fails.

An ask with one question can change its options while the session runs. A `live` call
with `options` (all of them, replacing the old ones) and optionally a new `question`
reloads the page. The user's pick and typed text stay where the option id still exists. The
call returns `"refreshed": {"dropped_picks": ["a"]}`, in this example the option id `a`
was picked and no longer exists. `question` and `options` fail for an ask with
`questions`.

The panel uses the page's text size. On a viewport 1672 px wide or more, it sits in the
gutter beside the question and the question stays centered. On a narrower one, it sits
under the form.

## License notices

The thought-bubble icon of the live `ask` is "Thought bubble" by SeregaCthtuf, from
[game-icons.net](https://game-icons.net/1x1/seregacthtuf/thought-bubble.html), licensed
under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The file
`src/pretty_prompt/assets/thought-bubble.svg` carries the same notice, and so does every
page that shows the icon.

## Markdown

A deliberate subset: headings, paragraphs, fenced and inline code, unordered and ordered
lists (nestable by indentation), blockquotes, GFM pipe tables (with `:--`/`:-:`/`--:`
alignment), links, images, bold, italic, and strikethrough. Anything outside it renders
as literal text.

A ` ```diff ` fenced block gets its own minimal rendering instead of a plain code block:
a colored left-edge rule and `+`/`-` markers per line, with removed lines struck through.

A ` ```mermaid ` fenced block renders as an actual diagram, centered under the
surrounding prose. This is the one deliberate exception to the page otherwise never
making a network call beyond its one POST: rather than vendor the mermaid library
(~3.5MB) into this package, a page with a mermaid fence loads a pinned build from
jsDelivr — never on a page without one. The diagram's theme is read once, from the
page's own resolved theme, at load; it does not follow the in-page toggle afterwards.
A diagram is drawn when it is first on screen, so one on a page or slide that starts
hidden is not drawn until it is shown.

The rendered view of a Markdown or AsciiDoc file in `review` draws diagrams the same way:
a ` ```mermaid ` fence in Markdown, and `[mermaid]` or `[source,mermaid]` on a listing in
AsciiDoc, the forms GitLab and GitHub draw. A comment on a diagram lands on the lines of
its fence. A review page without such a diagram still makes no network request.

Raw HTML is never passed through. Every run of source text is escaped before any tag is
added, so a `<script>` in a question appears as visible text. Rendering happens in Python
rather than in the page's JavaScript for that reason — the page is never handed a string
it has to trust.

Images use standard Markdown image syntax with an absolute path, as a `file://` URL or
bare (`/...` or `~/...`):

```markdown
![the current layout](file:///home/you/shot.png)
![the current layout](/home/you/shot.png)
```

A relative path renders as text: the server cannot know which directory it is relative to.

There is no separate images field and no base64 inlining. The page is opened from disk,
so `file://` resolves directly. URLs are restricted to `http`, `https`, `file`, `mailto`
and `data:image/`; anything else, `javascript:` above all, renders as text.

An `ask` or `show` page widens itself automatically when its content carries a
` ```diff `/` ```code ` fence or a table — the same content shapes that would otherwise
hit their own horizontal scrollbar early, at the normal width. This is a structural
check, not a length one: a merely long paragraph stays at the normal width, since prose
gets harder to read wider, not easier. Two or more image-bearing options trigger the
same widened layout (see `ask`, above). `review`'s own page always sizes itself to the
window instead, so it doesn't need this.

## How a call works

Every process keeps one shared browser tab (prototype), used by `ask`, `show`, and
`review` alike — an `ask` followed by a `show` followed by a `review` (in any order)
lands in the same tab, not three. A call tries to reuse it first, and the already-open
tab notices the new content and reloads in place, so a sequence of calls doesn't open a
new tab each time. A call that finds the shared tab already busy with another call falls
back to its own one-off tab instead of waiting for it, so two overlapping calls stay
safe. The one-off path is the original, always-safe behaviour:

A `show` page on the shared tab polls for updates from the moment it loads (`ask` and
`review`'s only start once their one answer is in, so a reload can't sweep away an
answer mid-composition — `show` has no answer to protect). That makes a later `show`
call reusing the same session a live update of the open tab: republish new content — a
progress readout, say — and the page picks it up within about 1.5 seconds, no new tab, no
action from the user.

1. A loopback socket binds an OS-chosen port on `127.0.0.1`, and a fresh
   `secrets.token_hex(16)` token is generated — both **before** the page is written, so
   they can be baked into its submit URL.
2. The page is written to a per-call temp directory and handed to the platform's opener
   (see above).
3. The call blocks on that socket until the token-matched POST arrives, a cancel arrives,
   or the page goes silent (abandoned). The temp directory is removed on every exit path.

A one-off `show` call that isn't waiting for Acknowledge skips step 1 and 3 entirely: no
socket, no token, nothing to block on — just a static page, written and opened. Its
Acknowledge button still shows, but with no listener behind it, a click is a local-only
mark rather than something sent anywhere.

Reusing the tab is checked, not assumed: an open tab reloads for a new page within about
1.5 seconds, and if none has after 4 seconds, the tab was closed, so a new one opens.
Every call then waits for some tab to load its page; if none does within 30 seconds, it
returns `not_delivered`. A tab the browser throttles hard in the background can miss the
4 seconds and end up with a second tab beside it, which is the cheaper mistake.

The page reports the answers entered so far back with a `progress` message that never
counts as the answer, so a cancelled or timed-out ask can be resumed with them prefilled.

The tab title gets a checkmark or cross prefix once its question is answered or
cancelled, so an answered tab left open (or waiting its turn, with tab reuse) is
identifiable without switching to it. The last chosen light/dark theme is remembered across processes and
browser profiles (a small state file, not just the browser's own `localStorage`) and
baked into the very first paint rather than corrected after the fact by script.

The token is the whole of the authentication: a `file://` page reports its origin as
`null`, so an `Origin` check cannot tell our page from any other local page that guessed
the port. This is the contract from the `no-text-wall` skill's `INTERACTIVE.md`, which
the page's submit script is adapted from; `assets/base.css` is vendored verbatim from the
same skill and kept byte-identical so it stays diffable against its source.

The blocking wait runs in a worker thread, off the server's event loop, so two
overlapping calls — `ask`, `show`, `review`, or a mix — open two tabs with their own
port and token. Nothing is serialised between them.

If an `ask` or `review` page cannot reach the listener — a stale token, or a call that
already timed out — it shows the answer (or the comments/overall note) in a selected,
read-only textarea rather than losing it. An `ask` page shows the same textarea, with
the answers so far, as soon as it notices its call timed out, without waiting for a
Send. It does not copy anything to the clipboard on
its own; copying is left to the user. `show`'s own fallback only reports the failure,
without a textarea — there is nothing to lose the way an unreachable answer would be,
since the report itself is still on screen either way.

## Developing

From the workspace root:

```bash
uv run --package pretty-prompt pytest
```

The tests drive the listener over real loopback HTTP and substitute a fake browser that
reads the generated page and posts back, so the port/token handshake and the page's own
markup are covered without a display.

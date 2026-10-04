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
| Linux desktop | `xdg-open` | `xdg-open` is missing, or neither `DISPLAY` nor `WAYLAND_DISPLAY` is set |
| WSL | `wslview` if installed, else `explorer.exe` | neither is on `PATH` (Windows interop off) |
| macOS | `open` | `open` is missing |
| Windows | `os.startfile` | never |

Under WSL, pages open in the Windows browser, not a Linux one inside WSLg, and no
display variable is needed. With the `explorer.exe` fallback, a one-shot page's file is
first translated to its `\\wsl.localhost\...` path with `wslpath -w`; explorer.exe's exit
status is ignored, since it reports 1 even on success, so a page that never opened
shows up as `not_delivered` instead. `file://` images on such a one-shot page still
point at Linux paths the Windows browser can't read; pages on the shared tab load
images over the loopback listener and are unaffected.

### Opener override

Set `PRETTY_PROMPT_OPENER` to a command line to open pages with that instead, for a
setup the table above gets wrong. The page's path or URL is appended as the last
argument; the command is split shell-style but not run through a shell. An override
also skips the display check. At launch, the first word must resolve on `PATH` (or be
a path to an executable), otherwise the server refuses to start:

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
  "allow_free_text": true,
  "timeout_seconds": 300
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

`allow_defer: true` (per question, or top-level for a single question) adds a "Not yet"
pill beside the question, for one the user may not be ready to settle because it depends
on something still open. Pressing it locks and clears the options, shows a "Deferred"
banner, and leaves the free-text box open for the reason; the answer counts as given for
Send/Next. That answer then carries `"deferred": true` and an empty `selected`. Only
answers to questions with `allow_defer` carry a `deferred` key at all.

`header` (prototype, `questions` only) is Markdown shown above the progress bar, so on
every step: typically what earlier rounds already settled, so each answer is given with
it in view. A long header collapses behind a "Context" toggle, which also says how many
items it holds when it is one list. Setting `header` on a single question is rejected;
put the context in `question` instead.

### Several questions on one page

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

### Streamed ask

With several questions, or long bodies, the user would otherwise wait while the agent
writes all of them. A streamed ask (prototype) sends them in chunks instead: the page
opens with the first chunk, and each later call adds its questions to that same page
without a reload, so nothing already answered is lost.

```json
{"questions": [{"question": "Which backend?", "options": [...]}], "more_coming": true}
{"ask_id": "3f2c9a1b7d4e8f60", "questions": [{"question": "Which region?"}], "more_coming": true}
{"ask_id": "3f2c9a1b7d4e8f60", "questions": [{"question": "Which plan?"}]}
```

A call with `more_coming: true` returns at once with
`{"ask_id": "...", "answers_so_far": [...]}`: one answer (the same shape as in
`answers`) per question the user has reached, so later questions can build on earlier
ones. The one the user is on may still be blank or change. The call that leaves
`more_coming` unset is the only one that waits, and it returns the usual `{"answers":
[...]}` for every question sent; its `questions` may be empty. Only the first call takes
`header`.

While more are coming, the progress bar says so, and the last question so far shows a
disabled "Waiting for the next question…" in place of Send. A cancel returns
`{"cancelled": true}` to the agent's next call and ends the stream. A stream holds its tab
between calls (its own tab if the shared one is busy); one the agent never finishes is
given up after 15 minutes without a call, freeing the tab.

### Result

Exactly one of:

```json
{"selected": ["pg"], "free_text": "", "expanded": []}
{"answers": [{"selected": ["pg"], "free_text": "", "expanded": []}, ...]}
{"cancelled": true}
{"timed_out": true}
{"not_delivered": true}
```

The first shape answers `question`; the second, one entry per question in order,
answers `questions`. Cancel is a control of its own on the page, so an answer the user
declined to give is distinguishable from one that never arrived. `timeout_seconds` is an
*idle* timeout, refreshed by any traffic from the page — including the probe it sends on
load — so the window starts when the page renders rather than when the browser was
launched. While the tab is visible the page sends a heartbeat, so however long the user
reads, the call stays alive; the timeout only runs while the tab is hidden or closed, and
a long multi-question page needs no larger value than a single question.

### Long waits and background tasks

Claude Code moves an MCP call still running after 120 s to a background task. The
answer then arrives as a task notification marked "NOT USER INPUT", which an agent must
not treat as the user's answer or approval, so it asks the user to repeat it in chat.
The limit belongs to Claude Code, not to `timeout_seconds`. Per the
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

`not_delivered` means the page never loaded in a browser, so the user never saw the
question. It comes back within about 30 seconds of opening the tab, not after the whole
timeout, so the agent can fall back to asking in the terminal. A page that loaded and was
then left alone still ends in `timed_out`.

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
  "wait_for_acknowledge": false,
  "timeout_seconds": 300
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
tab is still open, an `ask` or `review` opens a second tab instead of taking the first
over, so the report stays on screen. Once it is acknowledged, or its tab is closed, the
next call reuses the tab again and the report is gone. A newer `show` still replaces an
older one. When a question about the report follows, set `wait_for_acknowledge`, or
check `show_status` first.

### `show_status`

Takes no arguments; returns at once with `{"state": "unacknowledged" | "acknowledged" |
"none", "title": "..."}`. `none` means no report was shown in the shared tab, or
something else replaced it. A report that fell back to its own one-off tab (the shared
one was busy) has no listener, so it is not tracked and reads `none`.

### Result

```json
{"opened": true, "path": "..."}
{"acknowledged": true}
{"timed_out": true}
{"not_delivered": true}
```

Without `wait_for_acknowledge`, always `{"opened": true}`, plus `path` when the call
didn't reuse an already-open tab (see below). With it, `{"acknowledged": true}` once
Acknowledge is clicked, or `{"timed_out": true}` after `timeout_seconds` idle. Either
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
  "header": "Reviewing PR #42 — focus on the error-handling path.",
  "timeout_seconds": 300
}
```

Unlike `ask` and `show`, a `review` call's reaction doesn't reduce to a discrete choice
or a plain acknowledgement — it attaches to specific lines of a diff. It blocks the same
way `ask` does, waiting for the user to submit, cancel, or the idle timeout to elapse.

Each entry of `pages` is one file's diff, raw unified diff text (e.g. straight `git diff`
output) — a `@@ -a,b +c,d @@` hunk header is required so a line's coordinate can be
resolved; a diff with none is rejected before anything opens. `blurb` is optional
Markdown shown at the top of that page only, e.g. why that file changed. `header` is one
more Markdown block, shown above every page.

With more than one page, the user navigates freely between them (Prev/Next, arrow keys,
a progress bar); with exactly one, that chrome is skipped entirely rather than shown
inert.

Clicking anywhere on a diff line opens a text box anchored to it — a small bubble icon
marks a line as commentable on hover, but the whole row is the click/keyboard target, not
just the icon. Comments are single-line only; there is no range selection. An "Overall
note" box below the pages holds one free-text remark for the whole call, not tied to any
line. Once Submit or Cancel is sent, every line and the note box stop accepting further
input.

### Result

```json
{"comments": [{"path": "src/foo.py", "side": "new", "line": 2, "text": "why 2?", "context": "    return 2"}],
 "overall_note": "Looks fine overall."}
{"cancelled": true, "comments": [...], "overall_note": null}
{"timed_out": true}
{"not_delivered": true}
```

Submit always succeeds — an empty review (no comments, no overall note) is itself a
valid answer, since there's nothing to validate. Each comment is resolved server-side
from the diff you gave, not trusted from the page: `side` is `"old"` or `"new"`, `line`
is that side's 1-based line number in the file at `path` (not its position in the diff
text), and `context` is that line's own source text, so the agent doesn't have to
re-parse its diff to see what a comment was about.

Cancel returns the same shape as Submit, plus `"cancelled": true` — whatever comments and
overall note already existed are kept, not discarded, since the user may cancel
deliberately after a partial pass, intending the agent to act on what's already flagged.
This is the one deliberate difference from `ask`'s all-or-nothing cancel.

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

Raw HTML is never passed through. Every run of source text is escaped before any tag is
added, so a `<script>` in a question appears as visible text. Rendering happens in Python
rather than in the page's JavaScript for that reason — the page is never handed a string
it has to trust.

Images use standard Markdown image syntax with an absolute `file://` URL:

```markdown
![the current layout](file:///home/you/shot.png)
```

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
   or the idle timeout elapses. The temp directory is removed on every exit path.

A one-off `show` call that isn't waiting for Acknowledge skips step 1 and 3 entirely: no
socket, no token, nothing to block on — just a static page, written and opened. Its
Acknowledge button still shows, but with no listener behind it, a click is a local-only
mark rather than something sent anywhere.

Reusing the tab is checked, not assumed: an open tab reloads for a new page within about
1.5 seconds, and if none has after 4 seconds, the tab was closed, so a new one opens.
Every call then waits for some tab to load its page; if none does within 30 seconds, it
returns `not_delivered`. A tab the browser throttles hard in the background can miss the
4 seconds and end up with a second tab beside it, which is the cheaper mistake.

A streamed ask keeps its tab across calls instead: the page polls `/status` for new
steps, fetches them from `/steps` and appends them, and reports the answers so far back
with a `progress` message that never counts as the answer. If the shared tab is busy it
gets a session listener of its own, not a one-off one, since a one-off listener can't
outlive the call that opened it.

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
read-only textarea rather than losing it. It does not copy anything to the clipboard on
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

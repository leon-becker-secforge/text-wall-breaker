# pretty-prompt

An MCP server that asks the user a question in a browser page.

Claude Code's built-in AskUserQuestion renders inside the terminal panel: at most four
options, plain-text previews, no images. `pretty-prompt` opens a real page on the desktop
instead, so a question can carry any number of options, a Markdown body per option, and
images.

The server exposes one tool, `ask`. The name of the server is broader than its one tool
because more are expected.

## Installing

Register it with Claude Code, from a checkout of this repository:

```bash
claude mcp add pretty-prompt -- uv run --directory /path/to/python-libs --package pretty-prompt pretty-prompt-mcp
```

Or from the released wheel, with no checkout:

```bash
claude mcp add pretty-prompt -- uvx --index https://index.example.com/simple/ --from pretty-prompt pretty-prompt-mcp
```

The server refuses to start on a host it cannot show a page on. It checks at launch that
`xdg-open` is on `PATH` and that `DISPLAY` or `WAYLAND_DISPLAY` is set, and exits with
status 2 and a message on stderr if either is missing. That surfaces as a connection
error in the client rather than as a failure inside the first question asked.

### Accent colour

The page's accent — the selected-option border and tint, the submit button, focus
rings — defaults to green. Set `PRETTY_PROMPT_ACCENT` to a `#rrggbb` hex colour to use
something else instead, in both light and dark theme:

```bash
claude mcp add pretty-prompt --env PRETTY_PROMPT_ACCENT=#2563eb -- uv run --directory /path/to/python-libs --package pretty-prompt pretty-prompt-mcp
```

The text colour on top of the accent (e.g. the submit button's label) and the tint
behind a selected option are derived from the colour given, not configured separately.
An unset or invalid value falls back to the default green; an invalid one also makes
the server refuse to start, for the same reason as the `xdg-open`/`DISPLAY` checks
above — surfaced once at launch rather than as a page that silently ignored it.

## The `ask` tool

```json
{
  "question": "Which backend?\n\n![diagram](file:///tmp/arch.png)",
  "options": [
    {"id": "pg", "label": "PostgreSQL", "body": "Ships with the **operator**, so no extra service."},
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
collapses behind a click-to-open row instead of always being shown; `recommended: true`
pre-expands that row (without pre-selecting the option) for the one the user should read
first. Two or more image-bearing options switch the whole page to a widened, grid gallery
instead (comparing screenshots side by side beats one at a time behind a drawer), and
never collapse behind one. Every image, gallery or not, is click-to-zoom: it expands
full-size in a lightbox, dismissed by clicking again or Escape.

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

### Result

Exactly one of:

```json
{"selected": ["pg"], "free_text": "", "expanded": []}
{"answers": [{"selected": ["pg"], "free_text": "", "expanded": []}, ...]}
{"cancelled": true}
{"timed_out": true}
```

The first shape answers `question`; the second, one entry per question in order,
answers `questions`. Cancel is a control of its own on the page, so an answer the user
declined to give is distinguishable from one that never arrived. `timeout_seconds` is an
*idle* timeout, refreshed by any traffic from the page — including the probe it sends on
load — so the window starts when the page renders rather than when the browser was
launched.

`expanded` is the ids of options whose collapsed body was opened at any point before the
answer was sent. It stays set even if the drawer was closed again afterwards, so an id
can be expanded without being selected, or selected without ever being expanded (a short
option, or one never opened).

## Markdown

A deliberate subset: headings, paragraphs, fenced and inline code, unordered and ordered
lists (nestable by indentation), blockquotes, GFM pipe tables (with `:--`/`:-:`/`--:`
alignment), links, images, bold, italic, and strikethrough. Anything outside it renders
as literal text.

A ` ```diff ` fenced block gets its own minimal rendering instead of a plain code block:
a colored left-edge rule and `+`/`-` markers per line, with removed lines struck through.

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

## How a call works

Every process keeps one shared browser tab (prototype): a call tries to reuse it first,
and the already-open tab notices the new question and reloads in place, so a sequence of
`ask` calls doesn't open a new tab each time. A call that finds the shared tab already
answering another question falls back to its own one-off tab instead of waiting for it,
so two overlapping calls stay safe. The one-off path is the original, always-safe
behaviour:

1. A loopback socket binds an OS-chosen port on `127.0.0.1`, and a fresh
   `secrets.token_hex(16)` token is generated — both **before** the page is written, so
   they can be baked into its submit URL.
2. The page is written to a per-call temp directory and handed to `xdg-open`.
3. The call blocks on that socket until the token-matched POST arrives, a cancel arrives,
   or the idle timeout elapses. The temp directory is removed on every exit path.

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
overlapping `ask` calls open two tabs with their own port and token. Nothing is
serialised between them.

If the page cannot reach the listener — a stale token, or a call that already timed out —
it shows the answer in a selected, read-only textarea rather than losing it. It does not
copy anything to the clipboard on its own; copying is left to the user.

## Developing

From the workspace root:

```bash
uv run --package pretty-prompt pytest
```

The tests drive the listener over real loopback HTTP and substitute a fake browser that
reads the generated page and posts back, so the port/token handshake and the page's own
markup are covered without a display.

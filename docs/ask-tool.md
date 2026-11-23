# The `ask` tool

`ask` is the text-wall-breaker tool that puts a question to the user on a browser page
and waits for the answer. This document specifies its arguments, how the page behaves,
and the result it returns.

It does not cover installing the server or the page's accent colour and opener
settings. Those are given in the [README](../README.md). The live discussion that can
run beside an `ask` is described in [The `live` tool](live-tool.md).

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

`question` and each option's `body` are Markdown. `label` is short text with inline
Markdown only (bold, code, links), in a pick question and in a ranking alike. `id` is what
comes back in the answer, so an answer stays stable when a label is reworded.

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
one from an option. The session is described in [Live ask](live-tool.md#live-ask).

A question longer than 400 characters (`WIDE_QUESTION_CHARS`) widens the page to the
wide column. The limit is soft: nothing is rejected.

## Several questions on one page

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

### Ranking questions

A ranking question is a `questions` entry whose options the user orders instead of
picking. `rank` turns it on and selects the widget:

- `"rank": true` shows one ordered list ending in a cut line. The user drags rows or
  uses the arrow buttons, and moves the cut line the same way. The answer carries
  `order`, the option ids above the cut line, most important first, and `excluded`,
  the ids below it. The cut line starts last, so `excluded` is empty until the user
  moves it.
- `"rank": ["Now", "Next", "Later"]` shows one row per tier plus an Unsorted row. The
  answer carries `tiers`, a map of tier name to option ids, and `unsorted`.

```json
{
  "questions": [
    {"question": "Which approach?", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
    {"question": "Sort the follow-ups.", "rank": ["Now", "Later"],
     "options": [{"id": "tests", "label": "Tests"}, {"id": "docs", "label": "Docs"}]}
  ]
}
```

In this example, the second answer can be
`{"tiers": {"Now": ["tests"], "Later": []}, "unsorted": ["docs"], "free_text": "", ...}`.

A ranking answer has no `selected` and no `changed_from`. An ordered list sent
unchanged is a valid answer. A tiers question needs one option in a tier, or free text.
The ordered list shows each option's `body` and `caveat`; a tier chip shows the label
only. `rank` needs at least two options and must not be combined with `multi_select`,
`selected` or `recommended`. The top-level single question does not take `rank`.

## Glossary hints

If the server's working directory, or a folder above it up to the git root, holds a
`GLOSSARY.md` (else a `CONTEXT.md`), the page underlines the first occurrence of each
term per question and defines it in a popover on hover or keyboard focus. The nearest
file wins, so a library folder's own glossary beats the repo root's. Terms are matched as
whole words, case-insensitive, outside code. The file format is the one the
`domain-modeling` skill writes: `**Term**:`, then the definition.

`ask` takes `project`, a folder to look in instead of the working directory, for a git
worktree or a session started elsewhere. Start the server with `--no-glossary` to turn the
hints off (see [Options](../README.md#options)).

## Result

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

## Exporting an answer by hand

Every `ask` and `review` page has a download icon beside the theme toggle. It opens a
panel with the answer entered so far as the same JSON the page shows when Claude Code
stops waiting, and a Copy button. The user can paste that to Claude Code at any time,
for example before a long wait could end the call. The page stays open and usable, and
nothing is sent. The Copy button writes to the clipboard on the user's click; the page
never copies anything on its own. `show` has no icon: the user enters nothing there.

## Long waits and background tasks

Claude Code moves an MCP call still running after 120 s to a background task. The
answer then arrives as a task notification marked "NOT USER INPUT", which an agent must
not treat as the user's answer or approval, so it asks the user to repeat it in chat.
The limit belongs to Claude Code, not to text-wall-breaker. Per the
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
text-wall-breaker therefore sends a progress notification every 60 seconds, so the idle
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
> `src/text_wall_breaker/ask.py`; the tool description then stops mentioning them too.

# The `review` tool

`review` is the text-wall-breaker tool that shows a diff on a browser page and waits
for the user's line-anchored comments. This document specifies its arguments, the
diff page and the result it returns.

It does not cover the live session that can start from a review. That is described in
[The `live` tool](live-tool.md).

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

`repo` needs git 2.40 or newer. The repo is not trusted: the diff runs with `core.fsmonitor`
off and without the repo's `.gitattributes`, so no filter, textconv or diff driver from the
repo's config runs. A file whose attributes mark it binary or set its line endings is
therefore diffed as plain text.

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

## The `pp-review` prompt

The server offers one MCP prompt, `pp-review`. It asks the agent to find the current
branch's base, read the diff, call `review` with `repo` and a `git_range` of
`<merge-base>..HEAD`, and add `notes` on the most important changes. The optional `base`
argument names the base; without it the agent uses the branch's upstream, else the default
branch. Claude Code lists an MCP prompt as `/mcp__text-wall-breaker__pp-review`, with `base` as
the argument.

## Result

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
from a deleted row to an added one; see [ADR 0003](adr/0003-comment-ranges-carry-a-side-per-end.md)).
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


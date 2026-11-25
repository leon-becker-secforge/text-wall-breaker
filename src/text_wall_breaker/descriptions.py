"""What the agent reads about each tool: the server instructions and the tool descriptions.

Each description must stay under `MAX_TOOL_DESCRIPTION_CHARS`; per-field detail belongs in
the field's own `Field(description=...)`, which the cap does not cover.
"""

from __future__ import annotations

from .ask import SHARE_INTERACTION_SIGNALS

__all__ = [
    "ASK_DESCRIPTION",
    "INSTRUCTIONS",
    "LIVE_DESCRIPTION",
    "MAX_TOOL_DESCRIPTION_CHARS",
    "REVIEW_DESCRIPTION",
    "SHOW_DESCRIPTION",
    "SHOW_STATUS_DESCRIPTION",
]

# The MCP client silently truncates a tool's description at this length — a hard prefix
# cut, mid-sentence, with no signal to the agent that anything was cut.
# test_server.py's test_no_tool_description_risks_truncation keeps every registered tool
# a margin below it.
MAX_TOOL_DESCRIPTION_CHARS = 2048

INSTRUCTIONS = (
    "Browser pages for the user instead of terminal text. `ask` instead of "
    "AskUserQuestion: more than four options, an option that needs more than a line "
    "of explanation, or an answer easier to give while looking at an image. `show`: a "
    "result (report, comparison, long table) that would be a wall of text and needs "
    "no answer. `review`: a reaction that attaches to specific lines of a diff."
)

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS (see its own comment) rather than
# split across tools/fields, since an agent only ever sees the one description for a
# name it already knows to call.
ASK_DESCRIPTION = """Ask the user a question in a local browser page; blocks until answered,
cancelled, or timed out.

Images in Markdown: `![alt](/abs/path)` or `file:///abs/path`.
Paragraph breaks are real newlines, not the `\\n` escape.

Open `question` with a short bold lead sentence; context may follow in further
paragraphs, lists or blockquotes. Past 400 characters the page widens. A result `hint`
is formatting advice for your next ask.

Terms from the project's GLOSSARY.md (else CONTEXT.md) are defined on hover.

A long `body` sits behind a Details pill users often skip: put what could flip the
choice in `caveat`.

Returns one of:
  {"selected":[...], "free_text":"...", "expanded":[...]}   answered
  {"cancelled": true} / {"abandoned": true} / {"not_delivered": true}

`selected` and `free_text` are additive. `expanded`: option ids whose body was ever
opened. `not_delivered` (~30s): the user never saw it; ask again. `abandoned`: the
tab was closed with no answer; there is no deadline while it stays open. A cancelled or
abandoned ask carries `resume` when answers were entered.

`interrupted: true` on an answer: the user stopped the ask there for you to read now.
Its `free_text` may correct your plan or only constrain this answer: do what it says,
no more; its pick counts. The call returns at once; with `questions`, `answers` ends
at the last one given, plus `resume`: use it only if the rest still makes sense. If the
text changes them, ask anew.

With `questions`, returns `{"answers": [...]}`, one entry per question in order,
carrying the question's `id` when it set one; match by id. `skipped: true`: skipped.

`{"live": {"session": id}}`: the user opened a discussion about an option (or you set
`live`). Call `live` now, as `instruction` says; the answer comes through it. `live`
blocks like `ask` does, so it may also run past the client's background limit."""

# Only described when returned, so an agent never reads about fields it won't get.
if SHARE_INTERACTION_SIGNALS:
    ASK_DESCRIPTION += """

Every answer also has `changed_from` (option ids picked then abandoned) and each
`answers` entry has `visits` (times shown): private, weak signals for shaping what you
ask next."""

# Compressed to fit under MAX_TOOL_DESCRIPTION_CHARS with margin; see its own comment.
SHOW_DESCRIPTION = """Show the user a formatted result in a local browser page, for
anything that would be a wall of text in the terminal. Returns at once.

Markdown, the same subset as `ask` (headings, lists, tables, code, images,
blockquotes). Paragraph breaks are real newlines, not the `\\n` escape.

While the report is unacknowledged, any later call, `show` too, opens in a second tab,
so the report stays up. Before asking about the report, check `show_status` or set
`wait_for_acknowledge`.

Save is client-side only. With `wait_for_acknowledge` it returns
`{"acknowledged": true}` or, if the tab is closed, `{"abandoned": true}`.

Otherwise returns `{"opened": true, "path": "..."}` (`path` only for a one-off tab).
`{"not_delivered": true, "reason": "..."}`: the page never loaded; `reason` says why it
likely failed and whether a retry is worth it."""

REVIEW_DESCRIPTION = """Have the user review a diff in a local browser page, one file per page;
blocks until submit, cancel, or the tab is closed. For a reaction that reduces to a choice, use
`ask`.

Source, one of:
  - `repo`: the server runs `git diff` itself. Prefer it for anything in git: passing the
    diff as arguments puts a second copy of it in your context. The user can also show
    the hidden lines between hunks and comment on them, and a changed Markdown or AsciiDoc
    file gets a rendered view of the whole file; with `pages` neither, and the rendered
    view covers the hunks only. `notes` work with both sources.
  - `pages`: a diff you already hold.

The user comments on a line or a run of lines, can select text inside them, and can
leave a note per file and one overall note. A `notes` entry on a row the diff does not
show is an error.

Returns `{"comments": [...], "page_notes": [{"path", "text"}], "overall_note": "..." |
null}` on Submit (`page_notes` lists only files with a note). Each comment
is `{"path", "side", "line", "text", "context"}`: `side` "old" or "new", `line` that
side's 1-based line number in the file (not its position in the diff text),
`context` that line's text. A run of lines adds `end_side`/`end_line` (its last row;
the two ends may be on different sides) and `context` is every row of it, one per
line, each prefixed `+`, `-` or a space. A text selection adds `quote`, the selected
part. Cancel returns the same shape plus `"cancelled": true`,
keeping what was entered, since a cancel may be deliberate after a partial pass.
The same shape plus `"abandoned": true`: the tab was closed first; comments are
those entered so far. `{"not_delivered": true,
"reason": "..."}`: the page never loaded; `reason` says why and whether to retry.

`{"live": {"session": id}}`: the user started a live session (or you set `live`):
call `live` now, as `instruction` says."""

LIVE_DESCRIPTION = """Take part in a live session that `review` or `ask` started: post
replies (argument `replies`: a list of `{to, text}`; an unknown argument is an error),
then block until the user sends something new.

Returns `{"messages": [...]}`, what the user sent since your last call:
  - `{"thread": id, "path", "side", "line", "context", "text"}`: a new thread on lines
    (same fields as a review comment); `{"thread": id, "text"}`: a reply in it.
  - `{"chat": text}`: a request about the whole change; in an `ask` session also
    `option` when the user asked about one option, and with `questions` always `question`
    (its `id`, else position from 0).
  - `{"resolved": id}` / `{"reopened": id}`.
With `wait=false` it posts and returns at once: `{"posted": true, "messages": [...]}`.
Acknowledge a request that way, do the work, then call again.

Review sessions only: `refresh`, `git_range`, `paths`, `pages` and `notes` as on `review`.
Reply to a thread id or to "chat"; an `ask` session has one chat per question: "chat" for
one question, else the question's `id` (or position from 0). An `ask` session
with one question can replace `question` and `options`: the page reloads, and the result
has `"refreshed": {"dropped_picks": [...]}`, the option ids the user had picked that are
gone.

The session ends only through the user. The call that sees the end returns the result
of `review` or `ask` (with `cancelled`/`abandoned`/`interrupted` as there), plus `threads`
for a review and `chat` (every message) for an ask. A refresh sent after the end is not
applied: `"refresh_dropped": true`. Keep calling until that result arrives; do no other
work while the user waits on you."""

SHOW_STATUS_DESCRIPTION = """Whether the last `show` was acknowledged, without blocking: check it
before asking about a report shown without `wait_for_acknowledge`.

Returns `{"state": "unacknowledged" | "acknowledged" | "none", "title": "..."}`.
`none`: no report in the shared tab, or an `ask`/`review` has replaced it since. A
report that opened in its own one-off tab (shared tab busy, or holding an unread
report) cannot be tracked: its title comes as `own_tab`, and its acknowledgement is
unknown. `title` tells you which shared-tab report the state is about."""


def twb_review_prompt(base: str | None) -> str:
    """The text of the `twb-review` prompt: review the current branch against its base."""
    base_step = (
        f"The base is `{base}`."
        if base
        else "Find the base: the branch's upstream if it has one, else the repository's default "
        "branch (`git symbolic-ref refs/remotes/origin/HEAD`, else `main` or `master`)."
    )
    return f"""Have the user review the current branch against its base with the text-wall-breaker \
`review` tool.

1. {base_step} Use `git merge-base <base> HEAD` as the start, so the diff holds only this \
branch's changes.
2. Read the diff yourself first (`git diff <merge-base>..HEAD`) and pick the changes that matter \
most: behavior changes, risky or non-obvious logic, public interfaces, and anything the user \
could not guess from the file name. Skip mechanical changes (renames, formatting, generated \
files).
3. Call `review` with `repo` set to the repository root, `git_range` set to \
`<merge-base>..HEAD`, and `notes` holding one remark per important change: what it does and why. \
Keep each note short. Do not pass the diff as `pages`.
4. When the call returns, work through the user's comments and notes, and answer each."""

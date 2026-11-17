# text-wall-breaker

An MCP server that lets an agent ask the operator a question, show them a result, or have
them review an artifact, in a browser page instead of the terminal.

## Language

### Shared

**Header**:
Markdown shown above every question or page of a multi-step call, typically what is
already settled. Used by both an `ask` wizard and a `review` call.
_Avoid_: context, preamble, settled panel

### Asking

**Option**:
One choice offered for a question, identified by an id the operator never sees.
_Avoid_: choice, answer, item

**Body**:
The optional longer Markdown explaining an option. A long body sits behind a drawer.
_Avoid_: description, details

**Drawer**:
The collapsed state of a long body, opened by its own control, never by picking the option.
_Avoid_: accordion, expander, details

**Caveat**:
A short, always-visible line on an option naming the precondition or cost that could flip
the choice. Never hidden behind the drawer.
_Avoid_: note, tradeoff, warning, hint

**Recommended option**:
The option the agent leans towards. Marked visibly and read first, never chosen on the
operator's behalf. At most one per single-choice question.
_Avoid_: default, suggested, preselected

**Ranking question**:
A question whose options the operator orders instead of picking: one ordered list, or
sorted into named tiers. A kind of question inside `ask`, not a tool of its own; see
[ADR 0006](./docs/adr/0006-ranking-is-a-question-kind-in-ask.md).
_Avoid_: sort question, priority list, rank tool

**Tier**:
One named group of a ranking question, such as "Now" or "Later". Options left in no
tier come back as unsorted.
_Avoid_: bucket, column, lane

**Cut line**:
The divider in a ranking question's ordered list. Options below it are left out of the
ranking and come back as excluded. It starts last, so nothing is left out by default.
_Avoid_: separator, threshold, fold

**Interruption**:
The operator stopping an ask at one question because the agent should read that answer
now: the two are misaligned, or the answer matters too much to wait. Ends the ask at once
and always carries the operator's own words on that question. Distinct from cancelling,
which ends the ask with nothing to say.
_Avoid_: escalation, flag, raise, urgent answer

### Review

A `review` call presents one diff per page and blocks until the operator submits, so the
agent gets back exactly what was flagged, in one round trip. "Feedback" is the informal
umbrella term for this family of tools (`review` today, perhaps a slider tool later) —
never the name of a tool itself; see
[ADR 0001](./docs/adr/0001-review-is-a-separate-tool-from-ask.md). Ranking became a
question kind in `ask` instead; see
[ADR 0006](./docs/adr/0006-ranking-is-a-question-kind-in-ask.md).

**Page** (review):
One file's diff, submitted as raw unified diff text. The unit `review` paginates by,
freely navigable in either direction. Distinct from a `show` slide: a page carries an
addressable diff, not arbitrary content.
_Avoid_: slide, file, tab

**Blurb**:
Optional Markdown at the top of a single page, explaining that file's change. Distinct
from `header`, which spans every page of the call.
_Avoid_: description, intro, page context

**Gap**:
A stretch of unchanged lines a page's diff leaves out: before the first hunk, between two
hunks, or after the last. Shown as a bar the operator opens, whole or ten lines at a time.
Only a page whose file text is known has gaps, which means only the `repo` source; a
revealed line is an ordinary row a comment can address, but a comment never spans a gap
that is still closed.
_Avoid_: fold, collapsed section, hidden hunk

**Comment**:
A remark anchored to one line or a run of lines of a page's diff, addressed by path, side
(old/new), and line number in that file — not by its position in the diff text. A run
(a **range**) names a side on each end, so it may start on a deleted row and end on an
added one; see [ADR 0003](./docs/adr/0003-comment-ranges-carry-a-side-per-end.md).
Returned alongside its `context`: the anchored rows' own text, so the agent isn't
required to re-parse its diff to see what the remark was about.
_Avoid_: annotation, note

**Quote**:
The exact text the operator selected inside a comment's rows, returned only when it is
shorter than those rows. Tells the agent which part of a line the remark is about.
_Avoid_: selection, snippet, highlight

**Overall note**:
The one free-text remark the operator can leave for the whole call, in addition to any
per-page comments. Never a forced approve/reject verdict.
_Avoid_: summary, verdict, review body

**Page note**:
The one free-text remark the operator can leave for a single page, not tied to a line.
Sits between a comment (one place) and the overall note (the whole call).
_Avoid_: file note, page comment, summary

**Agent note**:
A remark the agent attaches to a line of a page's diff before the review opens, shown
inline for the operator to read. Read-only: the operator answers it with a comment of
their own. Distinct from a blurb, which explains the whole page.
_Avoid_: annotation, reasoning, explanation

**Rendered view**:
The new side of a changed Markdown or AsciiDoc page shown as formatted text, chunk by
chunk, each block beside the source rows it stands for. A comment on a block is an
ordinary comment on those new-side lines. Of the whole file when its text is known (a
`repo` source); otherwise of the lines the diff shows, with the lines between the hunks
left out.
_Avoid_: preview (reads as a preview of the result before Submit)

### Live review

A live session keeps one review or one `ask` open across many `live` calls, so the agent
can answer and change code while the operator reviews or decides; see
[ADR 0004](./docs/adr/0004-live-review-is-a-multi-call-session-with-one-result.md) and
[ADR 0005](./docs/adr/0005-a-live-session-owns-its-tab-and-ask-has-them-too.md).

**Live session**:
A review or an `ask` the operator and the agent work through together, from the attach
instruction until the operator sends the report or the answer, cancels or closes the tab.
Only the operator ends it. It owns its tab, so several can run at once.
_Avoid_: live mode (the page state, not the session), conversation

**Thread**:
A comment the operator sent to the agent, with the replies under it. Anchored like any
comment; only the operator resolves it. A comment that is not sent goes only into the
report. Live reviews only; a live `ask` has the chat.
_Avoid_: question (belongs to `ask`), query, discussion

**Chat**:
The one exchange of a live session not tied to a line, for requests about the whole
change. In a live `ask` it is the only exchange: one per page, and a message may name the
option it is about.
_Avoid_: overall note (the report's remark, written once)

**Reply**:
A Markdown message the agent posts to a thread or the chat. It may carry a done mark,
a hint for the operator to resolve the thread.
_Avoid_: answer (belongs to `ask`), response

**Refresh**:
The agent replacing a live session's pages after changing code: the server diffs the
repository again, or the agent sends new pages. A comment whose lines still exist moves
with them; any other comment is marked outdated and still returned.
_Avoid_: reload, update

# pretty-prompt

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

**Deferred question**:
A question the operator declines to settle yet because it depends on something still open.
Distinct from cancelling, which ends the whole ask.
_Avoid_: skipped, postponed, unanswered

**Interruption**:
The operator stopping an ask at one question because the agent should read that answer
now: the two are misaligned, or the answer matters too much to wait. Ends the ask at once
and always carries the operator's own words on that question. Distinct from cancelling,
which ends the ask with nothing to say, and from deferring, which keeps the ask going.
_Avoid_: escalation, flag, raise, urgent answer

### Review

A `review` call presents one diff per page and blocks until the operator submits, so the
agent gets back exactly what was flagged, in one round trip. "Feedback" is the informal
umbrella term for this family of tools (`review` today, perhaps a ranking/slider tool
later) — never the name of a tool itself; see
[ADR 0001](./docs/adr/0001-review-is-a-separate-tool-from-ask.md).

**Page** (review):
One file's diff, submitted as raw unified diff text. The unit `review` paginates by,
freely navigable in either direction. Distinct from a `show` slide: a page carries an
addressable diff, not arbitrary content.
_Avoid_: slide, file, tab

**Blurb**:
Optional Markdown at the top of a single page, explaining that file's change. Distinct
from `header`, which spans every page of the call.
_Avoid_: description, intro, page context

**Comment**:
A remark anchored to one line of a page's diff, addressed by path, side (old/new), and
line number in that file — not by its position in the diff text. Returned alongside its
`context`: that line's own text, so the agent isn't required to re-parse its diff to see
what the remark was about.
_Avoid_: annotation, note

**Overall note**:
The one free-text remark the operator can leave for the whole call, in addition to any
per-page comments. Never a forced approve/reject verdict.
_Avoid_: summary, verdict, review body

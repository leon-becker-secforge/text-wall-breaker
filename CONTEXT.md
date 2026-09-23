# pretty-prompt

An MCP server that lets an agent ask the operator a question, or show them a result, in a
browser page instead of the terminal.

## Language

### Asking

**Option**:
One choice offered for a question, identified by an id the operator never sees.
_Avoid_: choice, answer, item

**Body**:
The optional longer Markdown explaining an option. A long body sits behind a drawer.
_Avoid_: description, details

**Drawer**:
The collapsed state of a long body, opened by a click on its option.
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

**Header**:
Markdown shown above every question of a multi-question page, typically what is already
settled.
_Avoid_: context, preamble, settled panel

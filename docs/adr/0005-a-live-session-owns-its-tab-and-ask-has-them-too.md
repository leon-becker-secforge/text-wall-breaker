# A live session owns its tab, and `ask` has live sessions too

[ADR 0004](./0004-live-review-is-a-multi-call-session-with-one-result.md) kept a live
review on the shared tab, so at most one live session existed per server and it blocked
every other `ask`, `show` and `review` until it ended. That rules out a live `ask`: a
discussion about an option is short, and the operator would find every other question
queued behind it.

A live session now takes the listener it runs on out of the shared slot. The next call
binds a new shared listener and opens its own tab, so a live session blocks nothing and
several can run at once. The `live` tool finds a session by the id it is given. It
replaces `review_live` without an alias, and works for both kinds of session.

An `ask` session starts in the same two ways as a review: the operator presses the
thought bubble on an option, which makes the blocking `ask` return with an instruction to
attach, or the agent sets `live=true`. It has one chat per page. A message may name the
option it is about, which is the only anchor; the page is small enough that per-option
threads would add nothing.

The operator still sends the answer with the page's own buttons. Only the operator ends the session, by sending the answer, cancelling or
closing the tab. The `live` call that sees the end returns the result `ask` returns, plus
the messages of the chat.

Consequences:

- When a live session ends, it closes its listener. The tab keeps showing its last state,
  and a later call opens a new tab instead of reusing it.
- A call that had to use a one-off tab, because the shared tab was waiting on another
  call, cannot start a live session. Its page has no bubble, and `live=true` fails.
- The page shows the discussion in a side panel in the gutter beside the question, so the
  question stays centered. A viewport narrower than 1593 px shows it under the
  form.

Rejected:

- **One live session at a time, as for review.** A discussion would hold the shared tab
  and block other calls.
- **Threads per option, as a review has per line.** One chat with an option chip carries
  the same information with less to manage.
- **Letting the agent end the session.** An answer is always the operator's own.

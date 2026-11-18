# The `live` tool

`live` is the text-wall-breaker tool that the agent calls in a loop during a live
session. A live session is a `review` or an `ask` that the user and the agent work
through together on one page. This document specifies how a session starts, what each
call sends and returns, and how it ends.

It does not cover the tools that start a session. Those are
[The `review` tool](review-tool.md) and [The `ask` tool](ask-tool.md).

## Live session (`live`)

A live session is a `review` or an `ask` the user and the agent work through together. In
a review, the user asks about lines or requests changes while still reviewing, and the
agent answers and shows the changed diff on the same page (see [ADR 0004](adr/0004-live-review-is-a-multi-call-session-with-one-result.md)).
It needs the shared browser tab to start, so a `review` that had to open its own tab
cannot start one. Once started, the session owns its tab and leaves the shared one free,
so other calls open their own tab and several live sessions can run at once (see
[ADR 0005](adr/0005-a-live-session-owns-its-tab-and-ask-has-them-too.md)). `ask` has
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

## Live ask

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

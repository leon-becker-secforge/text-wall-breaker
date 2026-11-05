# Live review is a multi-call session that ends in one review result

A `review` call blocks until the operator submits, so the agent cannot answer a question
or apply a requested change while the operator is still reviewing. A live session is a
`review` the operator and the agent work through together: the operator asks in threads
or the chat, and the agent replies and refreshes the diff, until the operator sends the
report.

A live session starts in one of two ways. The operator presses the start control on an
open review page, which makes the blocking `review` call return with an instruction to
attach. Alternatively, the agent opens the review with `live=true`, and `review` returns
that instruction as soon as the page has loaded.

The agent then calls `review_live` in a
loop. Each call posts the agent's replies and refresh, then blocks until the operator
sends something new. With `wait=false`, a call posts and returns at once, so the agent
can acknowledge a request before it spends minutes on it.

The session ends only through the operator: Send report, Cancel (a partial result, as
ADR 0002 specifies) or a closed tab (`abandoned`). The `review_live` call that sees the
end returns the result `review` returns, plus a compact list of the session's threads.
An agent that handles `review` results therefore handles a live session's result too.

Rejected:

- **The server answers by itself**, through MCP sampling or a spawned
  `claude -p --resume --fork-session`. Sampling lacks the session's context. A fork has
  the context, but its answers and code changes never reach the agent that runs the
  session, and every question costs a full model start.
- **A push channel.** `review` returns at once, and each operator message reaches the
  chat as a notification, as `mcp-monitor` delivers its lines. The exchange is more
  immediate, but it depends on one client's notification support, and the final result
  arrives as a notification too. The agent then no longer gets back exactly what the
  operator flagged in one return value.
- **Answers to queued comments in the plain `review` call**, returned early and
  resumed like an interrupted `ask`. This covers questions but not refreshes. Every
  resume would also republish the page and lose the operator's unsent comments.

A live session holds the shared tab across many calls. At most one live session exists
per server: a second `review(live=true)` fails while one is active, and `review_live`
takes the session's id so that a stale id fails instead of attaching to a new session.

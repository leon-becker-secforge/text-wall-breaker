# How a call works

A call is one invocation of `ask`, `show` or `review`. This document describes how
the server opens a page, reuses the shared browser tab, and waits for the answer.

It does not cover the arguments or results of each tool. Those are given in
[The `ask` tool](ask-tool.md), [The `show` tool](show-tool.md) and
[The `review` tool](review-tool.md).

Every process keeps one shared browser tab (prototype), used by `ask`, `show`, and
`review` alike — an `ask` followed by a `show` followed by a `review` (in any order)
lands in the same tab, not three. A call tries to reuse it first, and the already-open
tab notices the new content and reloads in place, so a sequence of calls doesn't open a
new tab each time. A call that finds the shared tab already busy with another call falls
back to its own one-off tab instead of waiting for it, so two overlapping calls stay
safe. The one-off path is the original, always-safe behaviour:

A `show` page on the shared tab polls for updates from the moment it loads (`ask` and
`review`'s only start once their one answer is in, so a reload can't sweep away an
answer mid-composition — `show` has no answer to protect). That makes a later `show`
call reusing the same session a live update of the open tab: republish new content — a
progress readout, say — and the page picks it up within about 1.5 seconds, no new tab, no
action from the user.

1. A loopback socket binds an OS-chosen port on `127.0.0.1`, and a fresh
   `secrets.token_hex(16)` token is generated — both **before** the page is written, so
   they can be baked into its submit URL.
2. The page is written to a per-call temp directory and handed to the platform's opener
   (see *Installing* in the [README](../README.md#installing)).
3. The call blocks on that socket until the token-matched POST arrives, a cancel arrives,
   or the page goes silent (abandoned). The temp directory is removed on every exit path.

A one-off `show` call that isn't waiting for Acknowledge skips step 1 and 3 entirely: no
socket, no token, nothing to block on — just a static page, written and opened. Its
Acknowledge button still shows, but with no listener behind it, a click is a local-only
mark rather than something sent anywhere.

Reusing the tab is checked, not assumed: an open tab reloads for a new page within about
1.5 seconds, and if none has after 4 seconds, the tab was closed, so a new one opens.
Every call then waits for some tab to load its page; if none does within 30 seconds, it
returns `not_delivered`. A tab the browser throttles hard in the background can miss the
4 seconds and end up with a second tab beside it, which is the cheaper mistake.

The page reports the answers entered so far back with a `progress` message that never
counts as the answer, so a cancelled or timed-out ask can be resumed with them prefilled.

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
overlapping calls — `ask`, `show`, `review`, or a mix — open two tabs with their own
port and token. Nothing is serialised between them.

If an `ask` or `review` page cannot reach the listener — a stale token, or a call that
already timed out — it shows the answer (or the comments/overall note) in a selected,
read-only textarea rather than losing it. An `ask` page shows the same textarea, with
the answers so far, as soon as it notices its call timed out, without waiting for a
Send. It does not copy anything to the clipboard on
its own; copying is left to the user. `show`'s own fallback only reports the failure,
without a textarea — there is nothing to lose the way an unreachable answer would be,
since the report itself is still on screen either way.

# The `show` tool (prototype)

`show` is the text-wall-breaker tool that opens a report on a browser page for the
user to read. This document specifies its arguments, the shared-tab behavior and the
result it returns.

It does not cover the tool that waits for an answer, which is
[The `ask` tool](ask-tool.md).

```json
{
  "content": "# Review findings\n\n| file | severity |\n|---|---|\n| ask.py | high |",
  "title": "Review: auth middleware",
  "save_button": true,
  "acknowledge_button": true,
  "wait_for_acknowledge": false
}
```

`content` is Markdown, rendered the same way `ask`'s `question` is. Unlike `ask`, `show`
doesn't wait for anything by default — it opens the page and returns as soon as the
browser has it, so it's the tool to reach for when a result only needs to be read, not
answered: a report, a comparison, a diff, a table too long for a terminal reply.

The page carries two buttons, on by default, either can be turned off:

- **Save report** — downloads the content, as the original Markdown or as a static
  snapshot of the rendered page (no buttons, no scripts). Purely client-side; never
  reported back here.
- **Acknowledge** — marks the report seen. Set `wait_for_acknowledge` to block on it,
  the way `ask` blocks on an answer. Otherwise the click is remembered and readable
  through `show_status` (below).

## An unread report is not replaced

`show` shares its tab with `ask` and `review`. While a report is unacknowledged and its
tab is still open, any later call (`ask`, `review` or another `show`) opens a second
tab instead of taking the first over, so the report stays on screen. Once it is
acknowledged, or its tab is closed, the next call reuses the tab again and the report is
gone. When a question about the report follows, set `wait_for_acknowledge`, or
check `show_status` first.

## `show_status`

Takes no arguments; returns at once with `{"state": "unacknowledged" | "acknowledged" |
"none", "title": "..."}`. `none` means no report was shown in the shared tab, or
something else replaced it. A report that fell back to its own one-off tab (the shared
one was busy, or held an unread report) has no listener, so it is not tracked: the
result keeps describing the shared tab's report, or reads `none`. `title` tells them
apart.

## Result

```json
{"opened": true, "path": "..."}
{"acknowledged": true}
{"abandoned": true}
{"not_delivered": true}
```

Without `wait_for_acknowledge`, always `{"opened": true}`, plus `path` when the call
didn't reuse an already-open tab (tab reuse is described in
[How a call works](how-a-call-works.md)). With it, `{"acknowledged": true}` once
Acknowledge is clicked, or `{"abandoned": true}` if the tab is closed first. Either
way, `{"not_delivered": true}` means the page never loaded (see *Result* in [The `ask` tool](ask-tool.md)). A
one-off page without `wait_for_acknowledge` has no listener to tell, so it always
reports `opened`.

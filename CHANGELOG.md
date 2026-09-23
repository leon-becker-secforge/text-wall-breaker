# Changelog

All notable changes to `pretty-prompt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `caveat` on an `ask` option: one always-visible line of inline Markdown, shown as a
  warning-tinted strip under the label even while the body is collapsed, for whatever
  could flip the choice. Capped at 160 characters and one line; longer is rejected.

### Changed

- `recommended: true` now also shows a Recommended badge on the option, so it has a
  visible effect even on an option with a short body or none. More than one recommended
  option in a single-choice question is rejected.
- The `timeout_seconds` description now says what the page has always done: it keeps the
  call alive while visible, so the timeout only runs while the tab is hidden or closed.
  Agents had been raising it for long pages out of caution.

## [0.5.0] - 2026-09-21

### Added

- A ` ```mermaid ` fence in any `ask`/`show` Markdown renders as a real diagram — the one
  deliberate exception to "no network fetch but the one POST": the page loads a pinned
  mermaid build from jsDelivr rather than vendoring the ~3.5MB bundle, only on a page
  that actually has one. Diagram is centered under the surrounding prose.

### Fixed

- A non-blocking `show`'s reused tab only started polling for the next update after its
  Acknowledge button was clicked — for the (now default) fire-and-forget case, that never
  happened, so a second `show` call reusing the tab silently republished on the server
  while the open page kept showing stale content. It now polls from the moment it loads,
  the way `show`'s "live update the same tab" use case actually needs.

## [0.4.0] - 2026-09-21

### Added

- The `show` tool (prototype): opens a Markdown result in a browser page without waiting
  for an answer, for a report, a comparison, a diff, or anything else that would
  otherwise be a wall of text in the terminal. Returns as soon as the browser has the
  page, unless `wait_for_acknowledge` opts into blocking on the new Acknowledge button.
- `show`'s page carries a Save report button (Markdown or a static, button-free HTML
  snapshot, both client-side downloads) and an Acknowledge button, either toggleable off
  per call via `save_button`/`acknowledge_button`.
- `ask` and `show` now reuse the same process-wide shared tab: a `show` after an `ask`
  (or the reverse) lands in one tab instead of opening a second. The shared-tab session
  singleton moved out of `ask.py` into its own `session.py` to make that possible.

### Fixed

- A saved-as-HTML report captured the live page verbatim — Save/Acknowledge buttons,
  the save menu (open at the moment of capture), the theme toggle, and its own scripts
  included. It now saves a static content-only snapshot instead.

## [0.3.0] - 2026-09-20

### Added

- Blockquotes (`>`) in the Markdown subset, styled with a neutral left border and no
  fill, picked in a four-way visual A/B/C/D over an accent rule (with and without a
  tint background) and a bordered card.
- Nested unordered/ordered lists, inferred from indentation, replacing the previous
  single-level list buffer.
- Strikethrough (`~~text~~`), rendered as `<del>`.
- `expanded` alongside `selected`/`free_text` in the `ask` result: the ids of options
  whose Markdown body drawer was opened at any point before the answer was sent, latched
  so closing the drawer again afterwards (the expected motion with several long options)
  doesn't erase the signal.

### Fixed

- The wizard page's Back/Next had no matching CSS selector at all, so they rendered as
  plain unstyled browser buttons instead of matching the single-question page's Send
  answer/Cancel.

## [0.2.0] - 2026-09-20

### Added

- `questions` (prototype): ask several questions on one page instead of one, presented
  as a wizard — one question per screen, a progress bar, Back/Next — chosen over an
  all-at-once stack and an accordion in a visual A/B/C. Returns
  `{"answers": [{"selected", "free_text"}, ...]}` instead of `{"selected", "free_text"}`.
  Mutually exclusive with `question`/`options`/`multi_select`/`allow_free_text`.
- GFM pipe tables in the Markdown subset, with `:--`/`:-:`/`--:` alignment.
- Tab reuse (prototype): a process-lifetime shared browser tab answers sequential `ask`
  calls in place instead of opening a new tab each time; a call that finds the shared
  tab already busy still gets its own one-off tab, so overlapping calls stay safe.
- The tab title gets a checkmark or cross prefix once a question is answered or
  cancelled, so an answered tab left open (or waiting its turn, with tab reuse) is
  identifiable without switching to it.
- The last chosen theme is remembered across processes/browsers/profiles (a small state
  file next to XDG's other per-user state), not only in the browser's own `localStorage`,
  and is baked into the very first paint instead of corrected after the fact by script.
- ` ```diff ` fenced blocks render with a minimal style: colored left-edge rule, `+`/`-`
  markers, and a struck-through removed line — picked over a GitHub-style full-line
  background and gutter badges in a visual A/B/C.
- Two or more image-bearing options auto-switch to a widened, grid gallery layout
  instead of the stacked list, so several screenshots can be compared at a glance rather
  than one at a time behind a drawer.
- Every image (in a question, an option body, or a gallery) is click-to-zoom: it expands
  full-size in a lightbox, dismissed by clicking again or Escape.

### Changed

- Light mode's background softened from pure white to base.css's own off-white; cards
  and inputs stay pure white so they still lift off the page.

### Fixed

- The drawer's chevron (`›`) rendered as a stray control byte: its CSS `content: "\203A"`
  sat in a normal Python string, so Python's own escape parsing consumed the backslash
  before the browser ever saw the CSS.

## [0.1.0] - 2026-09-18

Initial release.

### Added

- The `ask` tool: opens a question as a page in the desktop browser instead of
  Claude Code's terminal-panel `AskUserQuestion`, for questions with more than four
  options, a Markdown body per option, or an image. Single- or multi-select options,
  an optional free-text box additive to any selection, and Markdown in the question
  and each option's body (headings, lists, links, images, inline and fenced code).
- `Option.recommended` pre-expands one option's body without pre-selecting it.
- A long option body collapses behind a click-to-open row; a short one (no image, no
  code block, under ~400 characters) is always shown inline.
- Light and dark themes, following the OS by default with a per-page toggle. Accent
  colour is configurable via the `PRETTY_PROMPT_ACCENT` environment variable.
- An idle-timeout heartbeat keeps a visibly open tab from timing out just because the
  user hasn't clicked anything yet.
- A startup check refuses to run on a host with no `xdg-open`, or with neither
  `DISPLAY` nor `WAYLAND_DISPLAY` set, so a headless host fails at connection time
  rather than inside the first question asked.
- A fallback path for a page that can't reach its listener (a stale token, or a call
  that already timed out): the answer is shown in a selected, read-only textarea
  rather than lost. Nothing is copied to the clipboard automatically.

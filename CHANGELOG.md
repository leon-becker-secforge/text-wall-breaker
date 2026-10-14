# Changelog

All notable changes to `pretty-prompt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `review`: `repo` (+ optional `git_range`, `paths`, `context_lines`) as a source instead of
  `pages`. The server runs `git diff` and makes one page per changed text file, so the diff
  no longer passes through the agent's context twice. `git_range` defaults to `HEAD`.
- `show_status`: a new tool that reports, without blocking, whether the last `show` in
  the shared tab was acknowledged (`unacknowledged`, `acknowledged` or `none`), so a
  caller can check a report was read before asking about it.
- `ask`: a question whose first paragraph is over 240 chars renders at regular weight with
  only its first sentence bold, instead of one bold wall. The result also carries a `hint`
  telling the agent to keep the question a short heading. The tool description says the same.

### Changed

- `ask`: `timeout_seconds` now defaults to 1800 for `questions`, `resume` and `ask_id`
  calls (300 for a single question), since a multi-question round outlasted 300 s.
- An `ask` or `review` no longer replaces an unacknowledged `show` in the shared tab:
  while the report's tab is open it gets a tab of its own. An acknowledged report, or a
  closed tab, is replaced as before. `show`'s description now says so and suggests
  `wait_for_acknowledge` when a question about the report follows.


- `ask`: prefill. `selected: true` on an `Option` starts it checked (several need
  `multi_select`), and `free_text` on a `Question` starts the free-text box with that
  text. A prefilled option is not counted in `changed_from`.
- `ask`: resuming. Every page now reports its answer as it changes (single-question and
  one-shot pages too, not only streamed asks). A cancelled or timed-out ask with
  something entered returns a `resume` id, and `ask(resume=id)` reopens the same
  questions and header with those answers prefilled. A deferral is not carried over.
  Drafts live in memory for an hour, at most 20, and resume once.

- `ask`: an optional `id` on each of `questions`, echoed as `id` on that question's entry
  in `answers`, so a result is matched by id instead of position. Must be unique within
  the ask, across the calls of a streamed one. Without it, answers are unchanged.

## [0.10.0] - 2026-09-30

### Added

- macOS, Windows, and WSL support: pages open with `open`, `os.startfile`, and the
  Windows browser (`wslview`, or `explorer.exe` with a `wslpath -w` translated file path)
  respectively, instead of the server refusing to start for lack of `xdg-open`. WSL no
  longer needs WSLg's display variables.
- `PRETTY_PROMPT_OPENER`: a command line to open pages with instead of the platform's
  default, with the target appended as its last argument. Validated at launch, like
  `PRETTY_PROMPT_ACCENT`.

## [0.9.0] - 2026-09-30

### Added

- `review`: a new tool that has the user comment on specific lines of a diff instead of
  answering a discrete question — a separate tool from `ask` rather than a mode of it,
  since the two interaction shapes don't compose (see `docs/adr/0001`). Paginates one
  file's diff per page (skipped entirely for a single page), whole-row click to comment,
  and an "Overall note" free-text box for the whole call. Cancel returns whatever
  comments/note already exist instead of discarding them (`docs/adr/0002`); each comment
  carries `context` (the diff line's own text) alongside the operator's remark.
- `ask`/`show` now widen to the existing gallery layout (920px) when their content
  includes a ```diff/code fence or a table — the same content shapes that hit their own
  horizontal scrollbar early, the same bug `review`'s width fix addressed.

### Fixed

- The MCP client silently hard-truncates a tool description past 2048 chars, mid-
  sentence, with no signal that anything was cut. `ask`'s description (4268 chars) was
  losing everything from partway through `expanded` onward — `allow_defer`, `questions`,
  streamed ask, and `changed_from`/`visits` were all invisible. `ask`, `show`, and
  `review`'s descriptions are now all well under the limit, with a regression test.
- The same `\n`-escape example in `ask`'s and `show`'s descriptions compiled to an actual
  newline character instead of the literal two characters it was meant to show.

## [0.8.0] - 2026-09-28

### Added

- A pending mark (❓) on an `ask`'s tab title as soon as the page loads, so the tab bar
  shows a question is open and waiting, not just an ordinary tab, without switching to
  it. Submit/cancel still overwrite it exactly as before.

### Fixed

- A literal two-character `\n` escape typed in a `question`/`content`/`body` field
  rendered as visible `\n` text instead of a line break. It's now expanded into a real
  line break outside fenced code blocks, where a literal `\n` is often the point (regex
  source, JSON shown as text) and expanding it would corrupt code meant literally.
- A reused-tab `ask`/`show` session serves its page over `http://127.0.0.1`, where a
  browser refuses to load a `file://` image embedded in Markdown — it silently rendered
  as blank space. The server now streams a local image back over that same `http://`
  origin instead of embedding the `file://` URL as-is; a one-shot page opened straight
  from disk is unaffected.

## [0.7.0] - 2026-09-24

Upgrading: reinstall the tool (`uv tool install --reinstall` on this directory; a plain
install is a copy, so a reconnect alone keeps the old code), then start a fresh Claude
Code session. A session that already loaded `ask`'s schema never sees `more_coming` or
`ask_id`, and its description doesn't mention `not_delivered`, although the server
returns it.

### Added

- `changed_from` on every `ask` answer: option ids picked and then abandoned, in the
  order first picked. Each entry of `answers` also gets `visits`, how often that
  question was shown. Both can be switched off with `SHARE_INTERACTION_SIGNALS` in
  `ask.py`.
- Streamed `ask` (prototype): `more_coming: true` opens the page with the questions
  written so far and returns an `ask_id` at once; later calls with that `ask_id` append
  their questions to the open page without a reload and return the answers so far, and
  the call without `more_coming` waits for the answers. The user starts answering while
  the agent is still writing.

### Fixed

- After the user closed the shared tab, every later `ask` and `show` in that process
  published into the closed tab and never opened a new one, so an `ask` waited out its
  whole timeout for a question nobody saw. The server now checks that a tab loaded each
  page: it opens a new tab when the old one doesn't pick the page up, and returns
  `{"not_delivered": true}` within about 30 seconds when no tab loads it at all.
- `_` and `__` only open or close emphasis at a word boundary, as in CommonMark, so
  identifiers like `set_mode and post_message` in prose stay literal instead of turning
  the text between them italic. `*` and `**` still work inside a word.
- An option label long enough to wrap stays beside its radio or checkbox, with the
  Recommended badge and chevron on the same row, instead of dropping onto a line below.

## [0.6.0] - 2026-09-23

Upgrading: start a fresh Claude Code session afterwards. A session that already loaded
`ask`'s schema keeps the old one even after an MCP reconnect, so it never sees `caveat`,
`allow_defer` or `header`, although the server accepts them.

### Added

- `show` badges (`!badge[text](success|warning|danger|info)`) and ` ```verdict ` blocks,
  both closed sets: an unrecognised value or fence stays literal text.
- `show` deck mode (prototype): `slides` instead of `content`, one Markdown document per
  slide with Prev/Next and arrow keys, plus optional `header`/`footer` around it.
- `caveat` on an `ask` option: one always-visible line of inline Markdown, shown as a
  warning-tinted strip under the label even while the body is collapsed, for whatever
  could flip the choice. Capped at 160 characters and one line; longer is rejected.
- `allow_defer` (prototype) on `ask` and on each entry of `questions`: a "Not yet" pill
  beside the question that locks the options and returns `deferred: true` with the reason
  in `free_text`, for a question that depends on something still open.
- `header` (prototype) on a multi-question `ask`: Markdown shown above every step, such
  as the decisions earlier rounds settled. A long one collapses behind a "Context" toggle.

### Changed

- `recommended: true` now also shows a Recommended badge on the option, so it has a
  visible effect even on an option with a short body or none. More than one recommended
  option in a single-choice question is rejected.
- The `timeout_seconds` description now says what the page has always done: it keeps the
  call alive while visible, so the timeout only runs while the tab is hidden or closed.
  Agents had been raising it for long pages out of caution.

### Fixed

- The wizard's free-text field had lost its bottom margin, leaving almost no gap above
  Back/Next/Cancel.
- The deck progress bar now clears the theme toggle, like the wizard's already did.

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

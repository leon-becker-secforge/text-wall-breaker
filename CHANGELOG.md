# Changelog

All notable changes to `text-wall-breaker` (named `pretty-prompt` up to 0.20.0) are
documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- `ask` rejects a question with no `options` that contains `"options":` or `"label":`.
  The page used to open with no choices and the JSON as text. An empty option list alone,
  or JSON quoted next to real options, is still accepted.
- The log file is rotated when the server starts instead of while it writes: every MCP
  client session runs its own server on the same file. An unknown `TEXT_WALL_BREAKER_LOG`
  level is reported on stderr.
- The `not_delivered` reason names the log file actually in use, or says no log is kept.

### Fixed

- A `file://` image whose path is percent-encoded (`a%20b.png`) loads in a reused tab.
- An answer from a reused tab is taken only from the page currently published, and only
  while a call waits on it; anything else gets HTTP 409 and a copy box. A second or stale
  tab could answer the newer question, and a page that loaded after its call returned
  `not_delivered` took an answer nobody read.
- `live` validates every reply before posting any, so a retry after one bad reply no longer
  duplicates the others.
- `live` with `git_range` or `paths` refreshes the diff; without `refresh` they were
  silently ignored.
- A second `live` call made while the first refreshes the diff is refused, as one made
  while it waits already was.
- A live session closes its listener outside its own lock, so a request arriving as the
  session ends cannot deadlock the call.

### Security

- `ask` and `show` pages fill their template in one pass, as `review` already did. A
  template placeholder name inside a question, option, title or report was substituted
  again, which let the text close an HTML attribute and add a script handler.
- `review` with `repo` no longer runs commands named in the repo's config:
  `core.fsmonitor` is off and attributes come from the empty tree, so no filter or diff
  driver runs. `repo` now needs git 2.40 or newer.
- The page listener serves each connection on its own thread with a 10 s socket timeout.
  One idle connection, such as a browser preconnect, stalled every page and kept a call
  from ever counting as abandoned.
- A request without the session token no longer counts as page activity, so it cannot keep
  an abandoned call alive.
- `GET /asset` serves only images a rendered page references, refuses symlinks, and sends
  `nosniff`, a sandbox CSP and no CORS header. It used to serve any image-named file,
  including a symlink to any file.
- The session token is redacted from the log, and the log file and its directory are
  created private to the user (`0600`/`0700`; an existing directory is tightened).
- A `show` report saved as HTML gets its local images inlined, so the file no longer
  carries the session token in their URLs.
- Every page carries a Content-Security-Policy with a per-page script nonce and a
  `no-referrer` policy, and loads mermaid with subresource integrity. `ask` and `show` no
  longer load remote images, which told their server the page was opened.
- The README names the Python patch releases that fix CVE-2025-6069 (3.12.12, 3.13.6).

## [0.21.0] - 2026-11-17

### Added

- A log file at `$XDG_STATE_HOME/text-wall-breaker/text-wall-breaker.log` (default
  `~/.local/state/text-wall-breaker/`), rotated at 1 MB. It records the opener's command and
  exit status, each page's delivery and each timeout. `TEXT_WALL_BREAKER_LOG` sets the level
  or `off`. Opener traces that went to stderr now go here.

### Changed

- `review` and `show` return a `reason` with `not_delivered`, as `ask` already did, and
  the reason now says the opener ran without error and whether a retry is worth it.
- **Breaking:** renamed from `pretty-prompt` to `text-wall-breaker`. The distribution is
  `text-wall-breaker`, the module `text_wall_breaker`, the console script
  `text-wall-breaker-mcp`. An MCP client registers the server under a new name, so tool
  names become `mcp__text-wall-breaker__<tool>`.
- **Breaking:** the environment variables `PRETTY_PROMPT_OPENER` and `PRETTY_PROMPT_ACCENT`
  are now `TEXT_WALL_BREAKER_OPENER` and `TEXT_WALL_BREAKER_ACCENT`.
- Moved out of the `python-libs` monorepo into its own repository. Installed from Git,
  not from a package index. Licensed under MIT.

## [0.20.0] - 2026-11-13

### Added

- `ask` and `show` pages color a fenced code block's syntax when the fence names a
  language Pygments knows (` ```python `, ` ```bash `, …), with the same palette as
  `review`. A fence without a language, or with one Pygments doesn't know, stays plain.
- `ask`: ranking questions. A `questions` entry with `rank: true` asks the user to
  order its options. A cut line in the list leaves the options below it out; the
  answer carries `order` and `excluded`. With tier names, such as
  `rank: ["Now", "Next", "Later"]`, the user sorts the options into those tiers, and the
  answer carries `tiers` and `unsorted`. Ranking questions mix with choice questions on
  one page and work with cancel, Stop here, export and `resume`. See
  [ADR 0006](docs/adr/0006-ranking-is-a-question-kind-in-ask.md).

- On a multi-question `ask` page the Next button stays enabled for an unanswered question
  and reads `Skip` (`Skip and send` on the last one). A skipped question's answer carries
  `skipped: true`, so it is distinct from free text typed as `-`.

### Changed

- Every tool rejects an argument it does not define with an error. Before, a misnamed
  one (`reply=` for `replies=`) was dropped and the call still succeeded. The removed
  `allow_defer` argument of `ask` is rejected too.
- The `live` and `ask` descriptions name the `replies` argument and say to ask anew when
  an interrupting message changes the questions.
- On `ask` pages the Recommended tag and the Details pill float at the top right of an
  option's label, and a long label wraps under them: from its second line on, the label
  runs the full width of the option instead of a narrow column beside the tag.

### Fixed

- On a multi-question `ask` page the progress bar stops short of the export icon instead
  of running under it.

## [0.19.0] - 2026-11-10

### Added

- `ask` and `review` pages have an export icon beside the theme toggle: a panel with the
  answer entered so far as the JSON the page shows when Claude Code stops waiting, and a Copy
  button. The user can hand the answer over by hand before a connection breaks; the page
  stays open and nothing is sent.
- The `pp-review` MCP prompt asks the agent to review the current branch against its base
  with `review`, annotating the most important changes. Optional argument: `base`.
- `review` shows a status letter per file, in the file list and above the diff: `A` added,
  `M` modified, `D` deleted, `R` renamed. `repo` reads it from git, `pages` from the diff text.
- `review` with `repo` detects renames: a renamed file is one page under its new path instead
  of a deleted and an added page. A rename without edits is a page with a single hunk line
  that says so. The file list filter also matches a renamed file's old path.
- `review` draws mermaid diagrams in the rendered view of a Markdown file (a ` ```mermaid `
  fence) and of an AsciiDoc file (`[mermaid]` or `[source,mermaid]` on a listing). A comment
  on a diagram lands on its fence's lines. The renderer loads from the CDN only on a page
  with a diagram, as on `ask` and `show`.

### Changed

- On a live-capable `ask` page the thought bubble sits beside the card, in the gutter where
  the question's bubble is, instead of in a slot inside every row. The Recommended tag and
  the Details pill are back at the row's right edge, and level with the label's first line
  when a long label wraps. A window narrower than 700px keeps the bubble inside the row.
- A mermaid diagram is drawn when it is first on screen, so a diagram inside a slide or
  page that starts hidden is no longer drawn with no size.

### Removed

- The "Not yet" control: the `allow_defer` parameter of `ask` (and of each entry of
  `questions`), the pill and its banner on the page, and the `deferred` key of an answer. A
  call that still passes `allow_defer` is no longer offered the control.

### Fixed

- Under WSL with `appendWindowsPath = false`, the server no longer refuses to start with
  "Windows interop may be disabled": it also looks for `/mnt/c/Windows/explorer.exe`, and
  when it finds nothing the message names that setting.
- The WSL `explorer.exe` opener writes an unexpected exit status or any stderr output to the
  server's stderr (the client's MCP log); before, a page that never opened left no trace but
  `not_delivered`.
- `ask` no longer scrolls sideways on long content:
  - a code block with a long line no longer widens the whole question;
  - a table wider than its column scrolls inside its own block;
  - an unbreakable word (a URL, an identifier) in a question, option label, body or caveat
    breaks inside its box instead of running under the Recommended tag, the Details pill or
    the next gallery card;
  - on a narrow window the Recommended tag and Details pill wrap below the label instead of
    squeezing it to a letter per line.
- Markdown with a link or image whose URL is never closed, such as `[t](https://example.com/...`
  followed by a space or the end of the text, no longer hangs the server. The URL pattern took
  exponential time: 26 characters cost seconds, 40 never finished.
- An interrupted `live` call no longer locks its session. The wait ended only when the user
  sent something, so every later call failed with "another live call is already waiting".
- A tool error raised while a call waits (`live`, `ask`, `review`, `show`) reaches the agent
  with its message. It arrived as a bare "Error executing tool", because the progress
  keepalive wrapped it in a task-group error that the handlers did not match.

## [0.18.0] - 2026-11-09

### Added

- `review` marks the characters that changed inside an edited line. A deleted line and the
  line that replaced it get the differing words in square boxes darker than the row. A
  changed word is marked whole; lines that look nothing alike are left alone.
- `show_status` names a report that opened in its own tab under `own_tab`; such a report's
  acknowledgement cannot be read, and it used to read as `none`.
- Every tool publishes an output schema.

### Changed

- `live` rejects the arguments of the other kind of session the same way for `ask` and
  `review` sessions (`paths: []` and `pages: []` now count as given).
- `review` with `repo` sends progress while it reads the diff.

### Fixed

- A failure while a `review` turns into a live session no longer leaves its tab claimed.

## [0.17.0] - 2026-11-08

### Added

- Glossary hints: `ask` underlines the first occurrence of each term of the nearest
  `GLOSSARY.md` (else `CONTEXT.md`) per question and defines it on hover. `ask` takes
  `project` to name the folder to search; `PRETTY_PROMPT_GLOSSARY=off` disables it.

### Changed

- `header` on a single `question` no longer errors: it is appended to the question.
- `not_delivered` from `ask` carries a `reason`.
- The `ask` description says that `live` blocks like `ask`.

### Fixed

- A long first paragraph put its `hint` into the shared `not_delivered` result, so every
  later undelivered `ask` carried a length hint that had nothing to do with it. An
  undelivered result never gets a hint now.

## [0.16.0] - 2026-11-06

### Added

- Live `ask` (ADR 0005): a thought bubble on each option asks the agent about it at once
  and opens a side panel for the answer and follow-ups, while the page stays open; `ask`
  also takes `live`. The panel uses the page's text size. The question stays centered,
  and the panel sits under the form on a viewport narrower than 1672 px. The icon is
  "Thought bubble" by SeregaCthtuf, game-icons.net, CC BY 3.0.
- `live` takes `options` and `question` for an `ask` session with one question: the page
  reloads, keeps the user's pick and typed text where the option id survives, and the call
  returns `refreshed.dropped_picks`.
- A question longer than 400 characters widens the page, so context after the lead
  sentence does not sit in the narrow column. The limit is soft.

### Changed

- `review_live` is now `live`, and serves both kinds of session. There is no alias.
- A live session owns its tab: it no longer blocks other calls, and several can run at
  once. When it ends, its tab is not reused for the next call.
- The `ask` description allows context after the bold lead sentence; it said "at most 2
  lines".

## [0.15.0] - 2026-11-05

### Added

- `review_live` and `review`'s `live` parameter: a live session. The user asks about
  lines or requests changes while reviewing, and the agent answers and refreshes the diff
  on the same page until the user sends the report (ADR 0004). The page gets a session
  card with a chat above the file list, an "Ask the agent now" switch in the comment box,
  threads under comments, a dot on files the agent changed and a "Since you looked" view
  of those changes. Comments follow their lines across a refresh or fold as outdated.

### Fixed

- `ask`, `review` and `show` with `wait_for_acknowledge` no longer end after 30 minutes of
  waiting. Claude Code aborts a stdio tool call that sends nothing for its idle window
  (`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, 30 minutes by default); a waiting call now sends a
  progress notification every 60 seconds. `review_live` does the same.
- `review`: a diff containing the page template's placeholders (`__LIVE_SCRIPT__`,
  `__DECK_SCRIPT__` and the like, as in a diff of `page.py`) no longer gets scripts
  inserted into its rows.

### Changed

- `review`: a Markdown or AsciiDoc page from `pages` gets a rendered view of the lines
  its diff shows. The view leaves out the lines between the hunks. Before this change,
  only a `repo` page had a rendered view.
- `review`: with the shared tab, a one-file review shows the file list too, since the
  session card sits at its top.

## [0.14.2] - 2026-11-04

### Changed

- Tool schema: the `ask` `questions[]` entries describe `question`, `options`,
  `multi_select` and `allow_free_text`; a `review` note's `side` and `end_side` are an
  enum (`"new"`, `"old"`) instead of free strings.
- Tool descriptions drop what the parameter descriptions already say (the `questions`,
  `resume`, button, `slides`, `pages` and `notes` restatements); the `review` description
  says `notes` work with both sources.

## [0.14.1] - 2026-11-04

### Added

- `review`: syntax coloring of the diff rows by file extension, with Pygments (new
  dependency). Old and new side of a hunk are lexed apart; a file type without a lexer
  stays plain.

- `review`: from 8 files on, the file list has a filter box and an "only with comments or
  a note" toggle; the list scrolls the current file into view.

### Changed

- `review`: diff rows wrap long lines (hanging under the text) and the page is up to
  1760px wide.
- `review`: the rendered view of a Markdown or AsciiDoc page is cut into chunks, one per
  block, with the block's source rows beside it. Blocks the diff does not touch fold behind
  an "N unchanged blocks" bar (neighbours of a change and blocks with a note stay open).
  The Diff | Rendered switch and the gap bars on such pages are gone.

## [0.14.0] - 2026-11-04

### Added

- `review`: a rendered view of a changed Markdown (`.md`, `.markdown`) or AsciiDoc
  (`.adoc`, `.asciidoc`) file when the source is `repo`. Beside the diff from 1000px wide,
  a Diff | Rendered switch below; a new file opens on the rendered one. A click on a block
  (shift-click extends) selects the source lines it stands for and the comment is the usual
  range comment on those new-side lines; dragging over text also records a `quote` (kept
  when it matches the source text) and marks it in the diff. Markdown renders with `markdown-it-py` (new
  dependency, raw HTML escaped); AsciiDoc needs `ruby` and `asciidoctor` and shows the diff
  only without them. Remote images are not loaded and local ones must be inside the repo.
- `review`: `notes`, the agent's own remarks on lines the diff shows (`path`, `side`,
  `line`, optional `end_side`/`end_line`, Markdown `text`). Each opens inline under its
  last row, with a chip on its first row to fold it and a bar to hide or show all;
  Reply opens the comment box on the note's rows. Read-only and not returned in the
  result. A note on a row the page does not show (wrong path, an unchanged line outside
  the hunks, an old-side line that is not deleted) is a tool error.
- `review`: hidden context can be expanded when the source is `repo`. Each stretch the
  diff leaves out (before, between and after the hunks) is a dashed bar with a pill: the
  middle shows it all, `↓ 10` and `↑ 10` show ten lines at a time from the hunk above or
  below. Revealed lines can be selected and commented with their real old and new numbers;
  a selection across a still-hidden stretch is refused. The page carries the file text
  (working tree, or the end of the range), up to 512 KB per file and 4 MB per call, and
  skips a file that is binary, not UTF-8, a symlink, or not what the diff was made from.
- `review`: comments on a run of lines and on selected text. Click a row, shift-click to
  extend, or select text; the rest of the diff dims and a box under the last selected row
  takes the comment (Ctrl+Enter adds, Esc clears). Comments show under their rows with
  Edit and Delete. The result gains `end_side`/`end_line` on a run (each end names its
  own side) and `quote` on a text selection; `context` of a run is every row prefixed
  `+`, `-` or a space. A single-line comment is unchanged. See ADR 0003.
- `review`: a file list that stays in view on a multi-page review: a left sidebar with
  one row per file (directory, current file marked, comment count, pencil for a page
  note), replacing the progress bar and Prev/Next arrows. Below 900px it becomes a drawer
  opened from a sticky bar with the current file and Prev/Next. `j`/`k` also switch files.
- `review`: a "Note on this file" box under each page's diff. The result gains
  `page_notes: [{"path", "text"}]` (only files with a note, `[]` otherwise) on submit,
  cancel and abandoned alike; `overall_note` is unchanged.

### Changed

- `review`: a slimmer top of the page. One row holds the header's leading heading (or
  "Review") and chips with the file, comment (live) and agent-note counts; the rest of the
  header is folded under "Details" when a heading stands in for it, and open when there is
  none. The path is a breadcrumb with a dim folder and a bold file name, and the blurb a
  single quiet line.
- `review`: one button language. The comment box's Comment and Clear take `ask`'s size,
  corners, type and hover; the gap pill is a 6px rectangle with `ask`'s type. Edit, Delete
  and Reply are quiet text (Delete turns red on hover) instead of outlined buttons. The
  Diff | Rendered switch and the notes' Hide all | Show all are labelled two-segment
  switches with a sliding green indicator; with some notes folded the notes switch shows
  neither side lit. The space above the bottom controls matches `show`'s 22px.
- `review`: small motion where the page used to jump, in `ask`'s 0.12 to 0.2s ease: a
  note, a comment, the comment box, revealed lines and a switched pane fade in from 4px
  above, and hover and selection colours ease. Nothing moves with reduced motion set.
- `review`: softer diff rows. An added or deleted row is tinted across, its line numbers
  sit on a stronger tint, and a `+` or `−` mark stands before the text, so the change no
  longer rests on colour alone. The numbers are wider, rows have more line height, the
  box has rounder corners and the hunk header more padding. The green and red are the
  ones used before, with brighter values in the dark theme.
- `review`: a row no longer opens its own comment box on click. The click selects the row
  and the comment dock opens under it; an unsent comment is no longer a textarea left open
  in the diff.
- `{"timed_out": true}` is now `{"abandoned": true}` on `ask`, `show` (with
  `wait_for_acknowledge`) and `review`. It means the page went silent for 3 minutes
  (tab closed, browser quit, machine asleep), not that an answer was slow.
- The page heartbeat is a fixed 15 s and also runs while the tab is hidden. It stopped
  while hidden before, so a user in another window lost the call after the timeout.
- `review`: an abandoned call returns the comments and overall note entered so far, like
  cancel. The page reports them with each heartbeat and 0.4 s after typing; before, a
  timeout returned nothing.
- `ask`: an abandoned call returns `resume` like a cancelled one, as `timed_out` did.

### Fixed

- `review`: switching file (list, arrows, `j`/`k`) left the comment box floating over the
  new file; it and the selection are now dropped on a switch.
- `review` with `repo` no longer fails on a file that is not valid UTF-8: `git diff` output
  is decoded with replacement characters instead of raising.

### Removed

- `timeout_seconds` on `ask`, `show` and `review`. A call no longer has a deadline: it
  waits as long as the page is open.

## [0.13.0] - 2026-11-04

### Changed

- Action row redesigned on `ask`, the wizard, `show` and `review`, from one shared stylesheet
  instead of four copies: solid accent primary, one outlined style for every other
  button (incl. `never mind`), dashed and unfilled when disabled (was 50% opacity, so a
  disabled primary looked like a faded accent button).
- Row is split: Cancel and Stop here left, Back and the primary right. The status text
  sits beside the primary so showing or hiding it never moves the buttons, and button
  labels no longer wrap on a phone-width page.
- The wizard's progress bar is one segment per question (answered or passed filled, current
  ringed). A segment before the current question is a button that goes back to it, the
  same as Back; later ones stay disabled so Next still checks each answer.
- Disabled and enabled buttons cross-fade (180ms) and the status text fades (160ms)
  instead of switching at once; both are off under `prefers-reduced-motion`.

### Fixed

- The progress bar fill never rendered on the wizard, slide deck and review pages: it was an
  inline `<span>`, which ignores `width` and `height`, so only the grey track showed. The
  deck and review fill is a block now; the wizard's bar is the segmented one above.

## [0.12.0] - 2026-10-29

### Removed

- Streamed `ask` (`more_coming`/`ask_id`): no agent ever used it, and it tripled the
  surface of `ask`'s code, docs and tests for a case plain multi-question `ask` already
  covers. `questions` on its own, sent in one call, is unaffected.

## [0.11.2] - 2026-10-28

### Fixed

- On a Linux desktop, a page is opened with Python's `webbrowser` first and `xdg-open`
  only if that finds no browser. With Firefox not yet running, `xdg-open` never returned,
  so a page that was on screen was reported as a failed start.
- `---`, `***`, `___` alone on a line now render as a horizontal rule instead of literal
  text, and no longer get misread as a table separator when the line above happens to
  contain a `|`.
- A long question's auto-bolded lead no longer corrupts text that already contains its
  own `**`/`__` markers, or splices `**` into the middle of a Markdown link whose text
  crosses the lead's first sentence boundary.
- Option labels now render inline Markdown (bold, code, links, etc.), matching captions
  and bodies, instead of only being HTML-escaped.
- A link or image URL containing a parenthesis no longer truncates the `href`/`src` and
  leaks the rest of the URL as plain text.
- A stray, unmatched run of backticks in prose no longer pairs into a bogus empty code
  span.
- A list item containing a `|`, immediately followed by a separator-shaped line, stays a
  list instead of being swallowed into a bogus table.

## [0.11.1] - 2026-10-20

### Added

- `ask`: "Stop here" ends an ask at the current question, for when the agent should read
  that answer now: the two of you are misaligned, or it can't wait. It asks for your note
  there (adding a text box where the question has none), then "Stop and send" returns at
  once with the answers so far, that one marked `interrupted: true`. With several
  questions the result ends at the last one answered and carries `resume` for the whole
  ask; a streamed ask returns it on the agent's next call.

### Changed

- `show`: a newer `show` no longer replaces an unacknowledged report in the shared tab;
  it opens a second tab, like `ask` and `review` already do.
- `ask`: a page that notices its call timed out (or the server is gone) now also shows
  the answers so far in the copy-and-paste box a failed Send shows, headed "Claude Code
  stopped waiting".
- `ask`: a picked single-choice option no longer says "click again to clear", which
  squeezed a long label into a narrow column. Hovering its row now empties the radio and
  slashes it instead, previewing the cleared state.

### Fixed

- Markdown images now accept a bare absolute path (`![x](/tmp/shot.png)`, `~/shot.png`) as
  well as a `file://` URL; agents often wrote the bare path, which rendered as text.

## [0.11.0] - 2026-10-17

### Added

- `ask`: clicking the selected option of a single-choice question clears it again, so
  you can get back to having nothing picked. The picked row says "click again to clear",
  left of a Recommended badge so the badge doesn't move.
- `ask`: once the call has ended without an answer (timed out, or the server is gone),
  the page says so as soon as you look at it again. It freezes, explains that the
  answers so far are kept for a resume, and marks the tab ⌛. `GET /status` now reports
  `open` so a reused tab can tell.

### Changed

- `ask`: a click anywhere on an option with a long body now selects it, the same as on any
  other option, instead of opening its body. The body opens from a "Details" pill on the
  right of the row, which replaces the chevron. Clicking an option no longer highlights
  its text.
- Tool, parameter and schema text rewritten for agents: duplicates removed (`allow_defer`,
  `caveat`, `slides`, `pages`), the "prototype" labels dropped, emdashes gone. Fixed: the
  `ask` description now says `cancelled` and `timed_out` carry `resume` (it named
  `not_delivered`), `questions` is rejected alongside `question` (it said "ignored"), and
  `review`'s `Page` fields are described instead of pointing at CONTEXT.md.

## [0.10.1] - 2026-10-14

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

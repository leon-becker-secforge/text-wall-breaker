# Changelog

All notable changes to `pretty-prompt` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

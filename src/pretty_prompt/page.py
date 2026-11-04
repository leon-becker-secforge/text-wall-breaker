"""Assembly of the HTML page one `ask` or `show` call opens.

The page is otherwise self-contained: styles are inlined, and the only network traffic
is the one POST back to the listener. The one deliberate exception is a page containing a
` ```mermaid ` fence (see `markdown.py`): it loads a pinned mermaid build from jsDelivr
to render it, rather than vendoring the ~3.5MB bundle into this package — see
`_MERMAID_SRC`/`_needs_mermaid`. Without that fence, nothing here ever reaches the
network beyond the one POST. `base.css` is vendored verbatim from the
no-text-wall skill (`assets/base.css`) for its `--ntw-*` colour tokens, which is the
part that generalises across a long-form report and a one-question prompt. Its
*components* (`.card`, `.theme-toggle`, ...) do not: they are sized for report
sections, and rendered oversized here on a first, un-reviewed pass. The addendum below
defines pretty-prompt's own compact components against the same tokens instead of
composing onto those classes. Keeping `base.css` itself byte-identical is still
deliberate — it stays diffable against its source until this repo forks it properly —
this page just stopped using the part of it that didn't fit.

Markdown arrives here already rendered and escaped by `markdown.py`. Everything else
that comes from the caller (option labels, ids) is escaped at the point of interpolation.
"""

from __future__ import annotations

import html
import json
import os
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from .diff import DiffLine, parse_unified_diff
from .markdown import (
    LONG_QUESTION_CHARS,
    bold_lead,
    first_paragraph,
    render_inline,
    render_markdown,
)

__all__ = [
    "ACCENT_ENV_VAR",
    "InvalidAccent",
    "build_multi_page",
    "build_page",
    "build_review_page",
    "build_show_page",
    "parse_accent",
    "read_saved_theme",
    "save_theme",
]

# Read by both server.py (to validate once at startup) and ask.py (to resolve the
# colour for each page) — defined here, not in server.py, so ask.py does not have to
# import from the module that already imports it.
ACCENT_ENV_VAR = "PRETTY_PROMPT_ACCENT"

# The theme toggle only ever writes localStorage, which is scoped to a `file://` page's
# storage partition — not guaranteed stable across browsers, profiles, or private
# windows, and useless to the reusable session page's own served origin, which is a
# different one again. A tiny state file next to XDG's other per-user state gives the
# preference one home that survives all of those, and that this process itself can read
# back before the first paint instead of flashing the wrong theme and correcting via JS.
_THEME_STATES = ("light", "dark")


def _theme_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "pretty-prompt" / "theme"


def read_saved_theme() -> str | None:
    """Return the last theme `save_theme` recorded, or None if there isn't one (yet)."""
    try:
        value = _theme_state_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if value in _THEME_STATES else None


def save_theme(theme: str) -> None:
    """Record `theme` for future pages to start in. Silently a no-op if that fails.

    Called from the listener's request-handling thread when a page's toggle reports a
    change — losing that write is better than crashing the one connection carrying it,
    since the theme is a convenience, not the reason the page exists.
    """
    if theme not in _THEME_STATES:
        return
    path = _theme_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(theme, encoding="utf-8")
    except OSError:
        return


# Where the accent tint sits under a selected option, mixed
# against each theme's own background rather than a fixed opacity on both: the dark
# background is already close to the accent's own darkness, so it needs a stronger mix
# to read as tinted at all, while the same strength on white would look garish.
_LIGHT_BG_RGB = (0xFF, 0xFF, 0xFF)
_LIGHT_TINT_ALPHA = 0.14
_DARK_BG_RGB = (0x1E, 0x1E, 0x1E)
_DARK_TINT_ALPHA = 0.22


class InvalidAccent(ValueError):
    """`PRETTY_PROMPT_ACCENT` is set but is not a usable colour."""


# The action row of every page type (ask, wizard, show, review), one stylesheet so the four
# cannot drift. Three looks only: solid accent (the one primary action), outlined (every
# other action), dashed and transparent (disabled, never a fill, so it cannot pass for an
# outlined button). Placed before the page's own addendum; the row is split: leave actions
# (Cancel, Stop here) left, forward actions (Back, primary) in `.pp-actions-end` right.
_ACTIONS_CSS = """
:root { --pp-btn-tint: #eef0f2; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --pp-btn-tint: #2e2e2e; } }
:root[data-theme="dark"] { --pp-btn-tint: #2e2e2e; }
.pp-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; position: relative; }
.pp-actions button { height: 34px; border-radius: 6px; padding: 0 14px; white-space: nowrap;
  font-family: var(--pp-font-mono); font-size: 0.85em; font-weight: 500; cursor: pointer;
  border: 1px solid var(--ntw-fg-muted); background: transparent; color: var(--ntw-fg);
  transition: background-color 0.18s ease, color 0.18s ease, border-color 0.18s ease,
    filter 0.18s ease; }
.pp-actions button:not(:disabled):hover { background: var(--pp-btn-tint); }
/* The status text lives inside this group, left of the primary, so showing or hiding it
   never changes the row's height. 260px basis: narrower than that and the group wraps
   onto its own line (status still beside the primary) rather than squeezing the text. */
.pp-actions .pp-actions-end { display: flex; align-items: center; justify-content: flex-end;
  gap: 8px; flex: 1 1 260px; min-width: 0; }
/* Every colour rule below is `:not(:disabled)`: an id selector otherwise outranks the
   `:disabled` rule and a disabled primary keeps its solid fill. */
#pp-submit:not(:disabled), #pp-next:not(:disabled), #pp-ack:not(:disabled) {
  border-color: var(--ntw-accent); background: var(--ntw-accent);
  color: var(--ntw-accent-contrast); }
#pp-submit:not(:disabled):hover, #pp-next:not(:disabled):hover, #pp-ack:not(:disabled):hover {
  background: var(--ntw-accent); filter: brightness(0.9); }
#pp-stop:not(:disabled) { border-color: var(--ntw-warning); color: var(--ntw-warning); }
#pp-stop-send:not(:disabled) { border-color: var(--ntw-warning); background: var(--ntw-warning);
  color: var(--ntw-bg); }
#pp-stop-send:not(:disabled):hover { background: var(--ntw-warning); filter: brightness(0.9); }
.pp-actions button:disabled { background: transparent; color: var(--ntw-fg-muted);
  border: 1px dashed var(--ntw-border); cursor: not-allowed; }
.pp-actions button[hidden] { display: none; }
/* One line, ellipsised: wrapping it would grow the row when it appears. */
#pp-status { flex: 1 1 0; min-width: 0; text-align: right; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; color: var(--ntw-fg-muted); font-size: 0.85em;
  transition: opacity 0.16s ease; }
#pp-status:empty { display: none; }
/* `hidden` fades the text out instead of removing it, so the page JS needs no change.
   `visibility` follows the fade (delayed) so a faded status is not read aloud. */
#pp-status[hidden] { display: block; opacity: 0; visibility: hidden;
  transition: opacity 0.16s ease, visibility 0s linear 0.16s; }
@media (prefers-reduced-motion: reduce) {
  .pp-actions button, #pp-status, #pp-status[hidden] { transition: none; }
}
"""

# Placed after base.css so it overrides rather than fights it. Every component here is
# pretty-prompt's own, sized for one short question rather than a long report — see the
# module docstring for why this stopped composing onto base.css's `.card`/`.theme-toggle`.
_ADDENDUM = """
* { box-sizing: border-box; }
:root {
  /* Monospace is confined to short, label-like text (option labels, buttons, code) —
     full-body monospace was tried and read as tiring for the Markdown prose an option's
     body actually carries. JetBrains Mono first because it is what this machine has;
     the generic families after it are the fallback chain if the page is ever opened
     somewhere else. */
  --pp-font-mono: "JetBrains Mono", ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;

  /* Light palette: base.css's default tokens read as flat next to real content, so this
     pushes contrast up — near-black text, a firmer border, and a deeper, more saturated
     accent than the vendored `--ntw-accent`. Pure white (#ffffff) for --ntw-bg was tried
     first and read as too bright/glary next to that firmer contrast — base.css's own
     off-white is gentler on the eyes while --ntw-bg-elevated stays pure white, so cards
     and inputs still lift off the page. */
  --ntw-bg: #fafaf9;
  --ntw-bg-elevated: #ffffff;
  --ntw-fg: #0a0a0a;
  --ntw-fg-muted: #54595f;
  --ntw-border: #c7cbd1;
  --ntw-accent: #1f7a4c;
  --ntw-accent-contrast: #ffffff;
  --ntw-verdict-bg: #e3f3ea;
  --ntw-code-bg: #f0f0f0;
}

/* Dark palette: pure black (the first pass) read as aggressive next to a white card —
   this keeps that pass's firmer borders and brighter accent but lifts the background to
   an editor-dark gray instead of true black. Same selectors as base.css's two dark
   rules, repeated here (not merged into them) so `base.css` itself stays untouched. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ntw-bg: #1e1e1e;
    --ntw-bg-elevated: #262626;
    --ntw-fg: #e6e6e6;
    --ntw-fg-muted: #9d9d9d;
    --ntw-border: #3f3f3f;
    --ntw-accent: #3ddc84;
    --ntw-accent-contrast: #00150a;
    --ntw-verdict-bg: #16281f;
    --ntw-code-bg: #2a2a2a;
  }
}
:root[data-theme="dark"] {
  --ntw-bg: #1e1e1e;
  --ntw-bg-elevated: #262626;
  --ntw-fg: #e6e6e6;
  --ntw-fg-muted: #9d9d9d;
  --ntw-border: #3f3f3f;
  --ntw-accent: #3ddc84;
  --ntw-accent-contrast: #00150a;
  --ntw-verdict-bg: #16281f;
  --ntw-code-bg: #2a2a2a;
}
/* 18px, not the 16px browser default: this page is read at arm's length next to an
   editor, not as a document, and the terminal/editor UI around it usually runs small
   already. Every other size in this file is em-based, so this one change rescales the
   whole page rather than being the first of many sizes to bump. */
html { font-size: 18px; }
body { margin: 0; padding: 20px 16px 40px; }
main { max-width: 560px; margin: 0 auto; position: relative; }

code { font-family: var(--pp-font-mono); font-size: 0.88em; background: var(--ntw-code-bg);
  padding: 0.1em 0.35em; border-radius: 4px; }
pre.code-block { font-family: var(--pp-font-mono); }
pre.code-block code { background: none; padding: 0; border-radius: 0; }

/* Picked in a four-way visual A/B/C/D over an accent rule (with and without a tint
   background) and a bordered card — neutral border, no fill, so a quote reads as
   de-emphasised source text rather than a highlighted callout. */
blockquote { border-inline-start: 4px solid var(--ntw-border); color: var(--ntw-fg-muted);
  margin: 0.6em 0; padding-inline-start: 14px; }
blockquote > p:first-child { margin-block-start: 0; }
blockquote > p:last-child { margin-block-end: 0; }

/* Picked over a bordered grid and a zebra-striped card in a three-way visual A/B/C —
   see the "table style" pick in project history. No vertical rules, a single accent
   rule under the header instead of a shaded background. */
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 0.9em; }
th, td { padding: 6px 10px; text-align: left; border: none;
  border-bottom: 1px solid var(--ntw-border); }
thead th { border-bottom: 2px solid var(--ntw-accent); color: var(--ntw-accent);
  font-weight: 600; text-transform: uppercase; font-size: 0.78em; letter-spacing: 0.03em; }
tbody tr:last-child td { border-bottom: none; }

/* Picked in the same round as the table style: minimal over a GitHub-style full-line
   background and a gutter-badge style — colour without a solid block, so it reads as
   text with an accent rather than a wall of tinted rows. */
.pp-diff { font-family: var(--pp-font-mono); font-size: 0.85em; margin: 0.6em 0;
  overflow-x: auto; }
.pp-diff .line { padding: 1px 10px 1px 12px; white-space: pre; border-left: 3px solid transparent; }
.pp-diff .add { border-left-color: #1f7a4c; }
.pp-diff .add::before { content: "+ "; color: #1f7a4c; }
.pp-diff .del { border-left-color: #b3261e; color: var(--ntw-fg-muted);
  text-decoration: line-through; }
.pp-diff .del::before { content: "- "; color: #b3261e; text-decoration: none; }
.pp-diff .ctx::before { content: "\\00a0\\00a0"; }
.pp-diff .hunk, .pp-diff .file { color: var(--ntw-fg-muted); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pp-diff .add { border-left-color: #3ddc84; }
  :root:not([data-theme="light"]) .pp-diff .add::before { color: #3ddc84; }
}
:root[data-theme="dark"] .pp-diff .add { border-left-color: #3ddc84; }
:root[data-theme="dark"] .pp-diff .add::before { color: #3ddc84; }

/* A rendered mermaid diagram is centered rather than left-aligned like the rest of the
   prose around it — it reads as a figure, not a paragraph. mermaid.run() replaces this
   element's content with an <svg> but keeps the <pre> itself, so the default monospace/
   white-space:pre styling a bare <pre> would otherwise carry is reset here too. */
pre.mermaid { margin: 0.6em 0; text-align: center; white-space: normal;
  font-family: inherit; }
pre.mermaid svg { max-width: 100%; height: auto; }

/* Auto-widened (see page.py's _is_gallery) rather than a manual flag, the same way a
   long option body already auto-collapses into a drawer without the caller asking. */
main.pp-wide { max-width: 920px; }

/* `.pp-options.pp-options-gallery`, not `.pp-options-gallery` alone: the element carries
   both classes, and matching both here beats the later, equal-specificity `.pp-options`
   rule below regardless of which one comes first in the stylesheet — a single-class
   selector lost that tie to source order once before. */
.pp-options.pp-options-gallery { display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; }
.pp-options-gallery .pp-option { display: flex; flex-direction: column-reverse; gap: 8px;
  padding: 10px; }
.pp-options-gallery .pp-option-body { margin: 0; }
.pp-options-gallery .pp-option-body img { width: 100%; margin-block: 0; }

/* Any image, gallery or not, expands full-size on click — a thumbnail-sized screenshot
   is otherwise too small to actually compare against another. */
main img { cursor: zoom-in; }
.pp-lightbox { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); z-index: 1000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
/* `[hidden]` alone loses this fight: author-origin CSS beats the browser's own default
   `[hidden] { display: none }` regardless of specificity, so the unguarded rule above
   left the overlay visible — and the page looking wrongly, uniformly dark — at all
   times, not just while zoomed. The extra attribute here outranks the plain class. */
.pp-lightbox[hidden] { display: none; }
.pp-lightbox img { max-width: 92vw; max-height: 92vh; border-radius: 6px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5); }

#pp-theme { position: absolute; top: -4px; right: 0; width: 30px; height: 30px; border: none;
  background: transparent; color: var(--ntw-fg-muted); border-radius: 999px; cursor: pointer;
  font-size: 15px; line-height: 1; display: flex; align-items: center; justify-content: center; }
#pp-theme:hover { background: var(--ntw-border); color: var(--ntw-fg); }

.pp-question { margin: 0 40px 14px 0; padding: 0; }
.pp-question > :first-child { margin-block-start: 0; font-weight: 600; font-size: 1.05em; }
.pp-question.pp-long > :first-child { font-weight: 400; font-size: 1em; }
.pp-question > :last-child { margin-block-end: 0; }
.pp-question > :not(:first-child) { color: var(--ntw-fg-muted); font-size: 0.9em; }
.pp-question img { max-width: 100%; height: auto; border-radius: 6px; }

.pp-question-row { display: flex; align-items: flex-start; gap: 10px; margin: 0 40px 14px 0; }
.pp-question-row > .pp-question { flex: 1; margin: 0; }
/* A wizard step's question sits below the progress bar, not level with the theme toggle,
   so the pill can run to the options' right edge. */
.pp-step .pp-question-row { margin-inline-end: 0; }
.pp-defer { flex: none; font-family: var(--pp-font-mono); font-size: 0.72em; font-weight: 500;
  border-radius: 999px; border: 1px solid var(--ntw-border); background: transparent;
  color: var(--ntw-fg-muted); padding: 2px 10px; cursor: pointer; white-space: nowrap; }
.pp-defer:hover { border-color: var(--ntw-warning); color: var(--ntw-warning); }
.pp-defer[aria-pressed="true"] { border-color: var(--ntw-warning); color: var(--ntw-warning); }
.pp-defer:disabled { opacity: 0.5; cursor: default; }
.pp-defer-banner { border-left: 3px solid var(--ntw-warning); background: #fdf3e6;
  padding: 6px 10px; font-size: 0.85em; border-radius: 0 6px 6px 0; margin: 0 0 8px; }
.pp-defer-banner[hidden] { display: none; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pp-defer-banner { background: #33280f; }
}
:root[data-theme="dark"] .pp-defer-banner { background: #33280f; }
.pp-options.pp-locked { opacity: 0.45; pointer-events: none; }

.pp-options { display: flex; flex-direction: column; gap: 6px; margin: 0 0 16px;
  padding: 0; list-style: none; }
.pp-option { border: 1px solid var(--ntw-border); border-radius: 6px;
  background: var(--ntw-bg-elevated); padding: 8px 10px; }
.pp-option:hover { border-color: var(--ntw-accent); }
.pp-option:has(input:checked) { border-color: var(--ntw-accent);
  background: var(--ntw-verdict-bg); }
/* An option with a body is a <details class="pp-option-drawer"> whose <summary> is the
   whole row. A click on the row selects, the same as on an option without a body (see
   the script below); only the Details pill opens the drawer. An option with no body is
   just the <label> below, un-nested. The row's text is not selectable: a quick second
   click (selecting, then clearing) otherwise highlights a word of the label. */
.pp-option-drawer > summary { list-style: none; cursor: pointer; }
.pp-option-drawer > summary::-webkit-details-marker { display: none; }
.pp-option-select { display: flex; gap: 8px; align-items: center; cursor: pointer; margin: 0;
  user-select: none; }
.pp-option-select input { margin: 0; accent-color: var(--ntw-accent); }
/* Grows into the row and wraps its own text: the row wraps (for the caveat below), so a
   label left at its natural width would drop onto a line of its own under the input. */
.pp-option-label { flex: 1 1 0; min-width: 0; font-family: var(--pp-font-mono);
  font-weight: 500; font-size: 0.9em; }
/* Picked over a muted line (read as body text, easy to miss) and warning-coloured text
   (read as harsh) in a three-way visual A/B/C: a warning-tinted strip running the full
   width of the option's bottom edge. It wraps onto its own line inside the select row
   (flex-basis 100% plus the row's padding), so it stays inside the <summary> a closed
   drawer still shows. The negative margins cancel .pp-option's padding so the strip
   meets the border; the bottom one only applies when nothing follows it in the row. */
.pp-option-select { flex-wrap: wrap; row-gap: 0; }
.pp-option-caveat { flex-basis: calc(100% + 20px); margin: 8px -10px 0;
  padding: 5px 10px 5px 33px; border-left: 3px solid var(--ntw-warning);
  background: #fdf3e6; color: var(--ntw-fg); font-size: 0.82em; font-family: inherit; }
.pp-option > .pp-option-select:last-child .pp-option-caveat,
.pp-option-drawer:not([open]) .pp-option-caveat { margin-bottom: -8px;
  border-radius: 0 0 5px 5px; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .pp-option-caveat { background: #33280f; }
}
:root[data-theme="dark"] .pp-option-caveat { background: #33280f; }
/* A gallery card has 10px padding all round, and column-reverse puts its select row at
   the bottom whatever follows it in the markup, so the strip always meets that edge. */
.pp-options-gallery .pp-option-caveat { margin: 8px -10px -10px; border-radius: 0 0 5px 5px; }
/* Picked over the solid show-style pill (loudest thing in the row, read as "already
   chosen") and a tab on the top border (cost vertical space) in a three-way visual
   A/B/C: small outlined accent caps pushed to the right edge, just before the Details
   pill. */
.pp-option-recommended { margin-inline-start: auto; font-family: var(--pp-font-mono);
  font-size: 0.68em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  color: var(--ntw-accent); border: 1px solid var(--ntw-accent); border-radius: 4px;
  padding: 1px 6px; flex: none; white-space: nowrap; }
.pp-option-recommended + .pp-option-details { margin-inline-start: 6px; }
/* A radio cleared by clicking it again (see the script) shows that only while its row is
   hovered: the radio empties and takes a slash, previewing the cleared state. Not a
   "click again to clear" line in the row: any placement of it either squeezed a long
   label into a narrow column or sat away from the radio. Drawn by hand because a native
   radio takes no background or pseudo-element in Firefox. :not(:disabled) keeps the
   slash off a page frozen after Send. */
.pp-option-select input[type="radio"] { appearance: none; width: 16px; height: 16px;
  flex: none; border: 2px solid var(--ntw-fg-muted); border-radius: 50%; cursor: pointer;
  background: transparent; transition: background .12s, border-color .12s; }
.pp-option-select input[type="radio"]:checked { border-color: var(--ntw-accent);
  background: radial-gradient(circle, var(--ntw-accent) 0 3.5px, transparent 4px); }
.pp-option-select input[type="radio"]:disabled { opacity: 0.5; cursor: default; }
.pp-option-select input[type="radio"]:focus-visible { outline: 2px solid var(--ntw-accent);
  outline-offset: 2px; }
.pp-option:hover input[type="radio"]:checked:not(:disabled) {
  border-color: var(--ntw-fg-muted);
  background: linear-gradient(-45deg, transparent calc(50% - 1px), var(--ntw-fg-muted)
    calc(50% - 1px) calc(50% + 1px), transparent calc(50% + 1px)); }
.pp-option-details { margin-inline-start: auto; flex: none; display: inline-flex;
  align-items: center; gap: 2px; padding: 1px 6px 1px 8px; border-radius: 999px;
  border: 1px solid var(--ntw-border); color: var(--ntw-fg-muted);
  font-family: var(--pp-font-mono); font-size: 0.68em; text-transform: uppercase;
  letter-spacing: 0.04em; }
.pp-option-details:hover { border-color: var(--ntw-accent); color: var(--ntw-accent); }
.pp-option-details svg { width: 13px; height: 13px; transition: transform .15s; }
.pp-option-drawer[open] .pp-option-details svg { transform: rotate(180deg); }
.pp-option-body { color: var(--ntw-fg-muted); font-size: 0.88em; margin: 8px 0 0 26px; }
.pp-option-body > :first-child { margin-block-start: 0; }
.pp-option-body > :last-child { margin-block-end: 0; }
.pp-option-body img { max-width: 100%; height: auto; border-radius: 6px; margin-block: 6px;
  display: block; }

.pp-free-text { display: block; margin: 0 0 16px; font-size: 0.9em; color: var(--ntw-fg-muted); }
.pp-free-text textarea { width: 100%; box-sizing: border-box; margin-block-start: 4px;
  border: 1px solid var(--ntw-border); border-radius: 6px; background: var(--ntw-bg-elevated);
  color: var(--ntw-fg); font: inherit; font-size: 0.92em; padding: 7px 9px; resize: vertical; }
.pp-free-text textarea:focus-visible { outline: 2px solid var(--ntw-accent); outline-offset: 1px; }

/* Interruption ("Stop here"): once pressed, the question's text box turns required and the
   warning-coloured "Stop and send" takes the primary button's place. */
.pp-free-text.pp-required textarea { border-color: var(--ntw-warning);
  box-shadow: 0 0 0 1px var(--ntw-warning); }

/* Danger, not neutral: this only ever appears when the answer failed to reach Claude
   Code, so it should read as a problem at a glance, not as just another content block.
   Border and title colour reuse base.css's own --ntw-danger (light #dc2626 / dark
   #f87171) rather than a new token; the tinted background is a second, literal colour
   per theme rather than color-mix(), matching the reasoning in base.css's own comment
   on .badge for not assuming color-mix() support. */
#pp-fallback { border: 1px solid var(--ntw-danger); border-radius: 6px;
  background: #fef2f2; padding: 12px 14px; margin-block-start: 16px; }
#pp-fallback strong { color: var(--ntw-danger); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) #pp-fallback { background: #3f1d1d; }
}
:root[data-theme="dark"] #pp-fallback { background: #3f1d1d; }
#pp-fallback textarea { width: 100%; box-sizing: border-box; margin-block-start: 8px;
  border: 1px solid var(--ntw-border); border-radius: 6px; background: var(--ntw-bg);
  color: var(--ntw-fg); font-family: var(--pp-font-mono); font-size: 0.85em; padding: 8px; }
"""

# Layered onto _ADDENDUM (not merged into it) only for the multi-question page: a wizard
# showing one question at a time, with a progress bar in place of the single-question
# page's one static question — picked over an all-at-once stack and an accordion in a
# three-way visual A/B/C (see the "paging style" pick in project history).
_MULTI_ADDENDUM = """
.pp-header { color: var(--ntw-fg-muted); font-size: 0.85em; margin: 0 40px 14px 0; }
.pp-header > :first-child, .pp-header-body > :first-child { margin-block-start: 0; }
.pp-header > :last-child, .pp-header-body > :last-child { margin-block-end: 0; }
details.pp-header > summary { cursor: pointer; list-style: none; font-family: var(--pp-font-mono);
  font-size: 0.92em; }
details.pp-header > summary::-webkit-details-marker { display: none; }
details.pp-header > summary::before { content: "\\203A"; display: inline-block; width: 1em;
  transition: transform .15s; }
details.pp-header[open] > summary::before { transform: rotate(90deg); }
.pp-header-count { border: 1px solid var(--ntw-border); border-radius: 999px; padding: 0 7px;
  margin-inline-start: 4px; }
.pp-header-body { margin-block-start: 6px; }
.pp-progress { display: flex; align-items: center; gap: 10px; margin: 0 40px 18px 0; }
.pp-progress-label { font-size: 0.8em; color: var(--ntw-fg-muted); white-space: nowrap; }
.pp-progress-track { flex: 1; height: 4px; border-radius: 999px; background: var(--ntw-border);
  overflow: hidden; }
.pp-progress-fill { height: 100%; background: var(--ntw-accent); border-radius: 999px;
  transition: width .15s; }
.pp-step { display: none; }
.pp-step.pp-step-active { display: block; }
.pp-step .pp-question { margin: 0 0 10px; }
.pp-step .pp-free-text { margin: 10px 0 16px; }
"""

_TEMPLATE = """<!doctype html>
<html lang="en"__THEME_ATTR__>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
</style>
</head>
<body>
<main__MAIN_EXTRA__>
  <button id="pp-theme" type="button" aria-label="Toggle theme">&#9680;</button>
__QUESTION_BLOCK__

  <form id="pp-form" novalidate>
__OPTIONS__
__FREE_TEXT__
    <div class="pp-actions">
      <button type="button" id="pp-cancel">Cancel</button>
      <button type="button" id="pp-stop">Stop here</button>
      <button type="button" id="pp-stop-undo" hidden>never mind</button>
      <div class="pp-actions-end">
        <span id="pp-status">__HINT__</span>
        <button type="button" id="pp-stop-send" hidden disabled>Stop and send</button>
        <button type="submit" id="pp-submit" disabled>Send answer</button>
      </div>
    </div>
  </form>

  <div id="pp-fallback" hidden>
    <strong id="pp-fallback-title">Couldn't reach Claude Code</strong>
    <p data-role="body" id="pp-fallback-reason"></p>
    <textarea readonly rows="6"></textarea>
  </div>
</main>

<div class="pp-lightbox" id="pp-lightbox" hidden><img id="pp-lightbox-img" alt=""></div>

<script>
(function () {
  var SUBMIT_URL = __SUBMIT_URL__;
  var TIMEOUT_SECONDS = __TIMEOUT_SECONDS__;
  var THEME_KEY = 'no-text-wall:theme';
  // So the tab bar shows whether this question is open or done without switching to the
  // tab — useful once several are open, or once one is answered and left to wait its
  // turn. The pending mark goes on right away; submit/cancel below replace it.
  var BASE_TITLE = document.title;
  document.title = '\\u2753 ' + BASE_TITLE;
  // Set only when this tab is reused across `ask` calls (see listener.py's
  // SessionListener) — null on the ordinary one-shot page, where startReusePolling()
  // below is then a no-op.
  var STATUS_URL = __STATUS_URL__;
  var GENERATION = __GENERATION__;

  // Any image (a gallery thumbnail or one in an option/question body) expands full-size
  // on click — a thumbnail is otherwise too small to actually compare against another.
  var lightbox = document.getElementById('pp-lightbox');
  var lightboxImg = document.getElementById('pp-lightbox-img');
  document.querySelectorAll('main img').forEach(function (img) {
    img.addEventListener('click', function (e) {
      e.stopPropagation();
      lightboxImg.src = img.src;
      lightboxImg.alt = img.alt;
      lightbox.hidden = false;
    });
  });
  lightbox.addEventListener('click', function () {
    lightbox.hidden = true;
    lightboxImg.src = '';
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !lightbox.hidden) {
      lightbox.hidden = true;
      lightboxImg.src = '';
    }
  });

  var form = document.getElementById('pp-form');
  var submitBtn = document.getElementById('pp-submit');
  var cancelBtn = document.getElementById('pp-cancel');
  var status = document.getElementById('pp-status');
  var freeText = document.getElementById('pp-free-text');
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input[data-option-id]'));
  var drawers = Array.prototype.slice.call(
    document.querySelectorAll('.pp-option-drawer[data-option-id]')
  );
  // `expanded` (below, in collect()) is "was this ever opened", not "is it open right
  // now" — with several long-bodied options, closing one after reading it to get to the
  // next is the expected motion, and a snapshot at submit time would read that as never
  // having been opened at all. The initial `open` attribute (recommended: true) counts
  // as having been shown too, seeded here rather than waiting for a first `toggle`.
  drawers.forEach(function (d) {
    d._wasExpanded = d.open;
    d.addEventListener('toggle', function () { if (d.open) d._wasExpanded = true; });
  });
  // `changed_from` (in collect()): every input ever checked, in first-checked order.
  // The server drops the ones still in the final selection, so what reaches the agent
  // is only what was picked and then abandoned. Only a user's own change counts: the
  // reload reset below and deferral uncheck without firing `change`.
  var picked = [];
  inputs.forEach(function (i) {
    i.addEventListener('change', function () {
      if (i.checked && picked.indexOf(i) === -1) picked.push(i);
    });
  });
  var frozen = false;

  // A reload on a reused tab (see STATUS_URL/startReusePolling below) always means a
  // genuinely new question, never the one just answered — but the browser doesn't know
  // that, and some (Firefox in particular) restore form field values across a plain
  // reload keyed by field identity/position. Since the new question's fields can occupy
  // the same identity as the old ones, that restore has to be undone before anything
  // else reads these fields, or the new question can silently start pre-answered. Undone
  // to the markup's own state (`defaultChecked`), which is a prefill when the agent set one.
  inputs.forEach(function (i) { i.checked = i.defaultChecked; });
  if (freeText) freeText.value = freeText.defaultValue;

  // Theme: the root attribute overrides the OS preference, absent follows it. The server
  // already set it from its own saved state (see page.py's __THEME_ATTR__) whenever it
  // has one; localStorage is only the fallback for the very first page it ever serves,
  // and stays as a same-tab-reload convenience after that.
  if (!document.documentElement.dataset.theme) {
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
    } catch (e) { /* private mode: fall back to the OS preference */ }
  }
  document.getElementById('pp-theme').addEventListener('click', function () {
    // rgb(30, 30, 30) is --ntw-bg's dark value (#1e1e1e) — kept in sync with the
    // palette in _ADDENDUM by hand, since this runs before that stylesheet's custom
    // properties can be read back out as anything other than a computed colour.
    var dark = getComputedStyle(document.body).backgroundColor === 'rgb(30, 30, 30)';
    var next = document.documentElement.dataset.theme
      ? (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
      : (dark ? 'light' : 'dark');
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* not persisted, still applied */ }
    // Fire-and-forget: the next page this process serves reads this back (see
    // page.py's read_saved_theme), so the preference survives past this one tab.
    send('theme', { theme: next });
  });

  // Deferral (allow_defer): the "Not yet" pill locks the options it sits beside and
  // clears any selection, since "not yet, but B" is not an answer the agent can act on,
  // while the free-text box stays open for the reason. Returns a function reading the
  // current state, so collect() can report it. Only called when the pill exists.
  function wireDefer(root, onChange) {
    var btn = root.querySelector('.pp-defer');
    if (!btn) return function () { return false; };
    var banner = root.querySelector('.pp-defer-banner');
    var list = root.querySelector('.pp-options');
    var text = root.querySelector('textarea[id^="pp-free-text"]');
    var boxes = Array.prototype.slice.call(root.querySelectorAll('input[data-option-id]'));
    var placeholder = text ? text.placeholder : '';
    var on = false;
    btn.addEventListener('click', function () {
      on = !on;
      btn.setAttribute('aria-pressed', String(on));
      btn.textContent = on ? 'Deferred, undo' : 'Not yet';
      banner.hidden = !on;
      if (list) list.classList.toggle('pp-locked', on);
      boxes.forEach(function (i) {
        if (on) i.checked = false;
        i.disabled = on;
      });
      if (text) text.placeholder = on ? 'Why not yet? (optional)' : placeholder;
      onChange();
    });
    return function () { return on; };
  }
  // <main>, not the form: the pill sits beside the question, which is outside it.
  var isDeferred = wireDefer(document.querySelector('main'), function () { refresh(); });

  function collect() {
    return {
      selected: inputs.filter(function (i) { return i.checked; })
                      .map(function (i) { return i.dataset.optionId; }),
      free_text: freeText ? freeText.value.trim() : '',
      // Every drawer opened at any point this page has been up, not just the ones still
      // open at submit time — see the `_wasExpanded` tracking above.
      expanded: drawers.filter(function (d) { return d._wasExpanded; })
                        .map(function (d) { return d.dataset.optionId; }),
      changed_from: picked.map(function (i) { return i.dataset.optionId; }),
      deferred: isDeferred()
    };
  }

  // Free text is additive, never exclusive: either a checked option or a non-empty box
  // is enough on its own to make the answer sendable.
  //
  // The hint only shows while the form isn't answerable yet: once something is picked
  // or typed, "Pick an option, write an answer, or both." has done its job and stays
  // out of the way rather than sitting there as clutter for the rest of the call.
  function refresh() {
    inputs.forEach(function (i) { i._wasChecked = i.checked; });
    var d = collect();
    submitBtn.disabled = !d.deferred && d.selected.length === 0 && d.free_text === '';
    status.hidden = !submitBtn.disabled;
    reportProgress();
  }

  // The answer so far, kept by the server so an ask that times out or is closed can be
  // resumed with it. Debounced: refresh() runs on every keystroke in the free-text box.
  var progressTimer = null;
  function reportProgress() {
    clearTimeout(progressTimer);
    progressTimer = setTimeout(function () { send('progress', collect()); }, 400);
  }

  inputs.forEach(function (i) { i.addEventListener('change', refresh); });
  // A radio has no native way back to nothing picked, so a click on the one already
  // checked clears it. The browser checks a radio before its click event fires, so
  // `_wasChecked` holds the state from before the click; refresh() resyncs it after
  // every change, including the ones code makes (prefill, deferral).
  inputs.forEach(function (i) {
    if (i.type !== 'radio') return;
    i.addEventListener('click', function () {
      if (!i._wasChecked) return;
      i.checked = false;
      i.dispatchEvent(new Event('change'));
    });
  });
  // An option with a long body is a <details> whose <summary> is the whole row (see
  // page.py's _render_options), so a click on the checkbox/radio would otherwise also
  // reach <summary> and toggle the drawer open. Stopping it here is what keeps
  // selecting and opening independent.
  inputs.forEach(function (i) {
    i.addEventListener('click', function (e) { e.stopPropagation(); });
  });
  // The rest of a drawer's row selects too, like an option without a body. Only the
  // Details pill opens the drawer, and the keyboard: Enter on the focused row arrives as
  // a click with detail 0.
  function wireSummary(s) {
    var input = s.querySelector('input');
    s.addEventListener('click', function (e) {
      if (e.detail === 0 || e.target.closest('.pp-option-details')) return;
      e.preventDefault();
      if (!input.disabled) input.click();
    });
  }
  document.querySelectorAll('.pp-option-drawer > summary').forEach(wireSummary);
  if (freeText) freeText.addEventListener('input', refresh);

  // The page's side of the shared Interruption script below.
  function stopTarget() {
    return { root: form, before: form.querySelector('.pp-actions'), id: 'pp-free-text' };
  }
  function stopMode(on) {
    submitBtn.hidden = on;
    if (on) status.hidden = true;
    else refresh();
  }
  function stopPayload(stop) {
    var data = collect();
    data.free_text = stop.box.value.trim();
    data.interrupted = true;
    return data;
  }
  // Interruption ("Stop here", see CONTEXT.md): ends the ask at once with the answers so
  // far and the current question marked, so the agent reads it before anything else. It
  // needs the user's own words there: the question's text box turns required, and one is
  // added where the question has none (allow_free_text off). stopTarget, stopMode and
  // stopPayload above are each page's side of it.
  var stopBtn = document.getElementById('pp-stop');
  var stopSend = document.getElementById('pp-stop-send');
  var stopUndo = document.getElementById('pp-stop-undo');
  var stopping = null;
  function syncStop() {
    stopSend.disabled = frozen || !stopping || stopping.box.value.trim() === '';
  }
  stopBtn.addEventListener('click', function () {
    var target = stopTarget();
    var box = document.getElementById(target.id);
    var made = !box;
    if (made) {
      var holder = document.createElement('label');
      holder.className = 'pp-free-text';
      holder.appendChild(document.createTextNode(''));
      box = document.createElement('textarea');
      box.id = target.id;
      box.rows = 3;
      holder.appendChild(box);
      target.root.insertBefore(holder, target.before);
    }
    var label = box.parentNode;
    stopping = { box: box, made: made, index: target.index, label: label.firstChild.nodeValue };
    label.firstChild.nodeValue = 'Why stop here? Claude reads this first.';
    label.classList.add('pp-required');
    box.addEventListener('input', syncStop);
    stopBtn.hidden = true;
    stopSend.hidden = false;
    stopUndo.hidden = false;
    stopMode(true);
    syncStop();
    box.focus();
  });
  stopUndo.addEventListener('click', function () {
    var label = stopping.box.parentNode;
    stopping.box.removeEventListener('input', syncStop);
    if (stopping.made) label.remove();
    else {
      label.classList.remove('pp-required');
      label.firstChild.nodeValue = stopping.label;
    }
    stopping = null;
    stopBtn.hidden = false;
    stopSend.hidden = true;
    stopUndo.hidden = true;
    stopMode(false);
  });
  stopSend.addEventListener('click', async function () {
    if (stopSend.disabled) return;
    var data = stopPayload(stopping);
    freeze('Sending\\u2026');
    if (await send('interrupt', data)) {
      freeze(doneMessage('Stopped here. Claude reads your note first.'));
      document.title = '\\u270b ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });
  function freezeStop() {
    if (stopping) stopping.box.readOnly = true;
    stopBtn.disabled = true;
    stopSend.disabled = true;
    stopUndo.hidden = true;
  }

  function freeze(message) {
    frozen = true;
    inputs.forEach(function (i) { i.disabled = true; });
    document.querySelectorAll('.pp-defer').forEach(function (b) { b.disabled = true; });
    if (freeText) freeText.readOnly = true;
    freezeStop();
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    // Unlike refresh()'s hint, this message (sending/sent/cancelled) is never hidden —
    // it's always relevant, not just while the form is unanswerable.
    status.hidden = false;
    status.textContent = message;
    stopHeartbeat();
  }

  // The server's timeout is idle, not absolute — bumped by any request it sees,
  // including a 'probe' (see listener.py). Without this, someone who opens the page and
  // reads for a while before touching anything gets timed out for no better reason than
  // not having clicked yet. Interval scales with the actual timeout instead of a fixed
  // number, so a short timeout_seconds still gets pinged well before it would lapse.
  // Only while the tab is visible: minimising it or switching away is meant to still
  // let an abandoned call time out rather than being kept alive forever unattended.
  var HEARTBEAT_MS = Math.min(60000, Math.max(2000, (TIMEOUT_SECONDS * 1000) / 4));
  var heartbeatTimer = null;
  // On a reused tab GET /status keeps the call alive the same way a probe does, and
  // also says whether a call still waits on this page. A one-shot page learns that its
  // call ended from the probe failing, since its listener closes with the call.
  async function heartbeat() {
    if (document.visibilityState !== 'visible') return;
    if (!STATUS_URL) { send('probe', {}); return; }
    try {
      var res = await fetch(STATUS_URL);
      if (!res.ok) return;
      var body = await res.json();
      if (body.generation !== GENERATION) expire(false);
      else if (!body.open) expire(true);
    } catch (e) { expire(false); }
  }
  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(heartbeat, HEARTBEAT_MS);
  }
  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }
  document.addEventListener('visibilitychange', function () {
    // Catches up immediately on refocus, rather than waiting up to HEARTBEAT_MS for the
    // next tick, in case the deadline is close by the time the tab is looked at again.
    if (document.visibilityState === 'visible') heartbeat();
  });

  // The call this page answers ended without an answer: it timed out while the tab was
  // hidden, or the server is gone. Freezing beats taking an answer nobody will read. On a
  // reused tab still showing its own generation, the next question can still land here.
  function expire(reusable) {
    if (frozen) return;
    freeze('Claude Code stopped waiting for this answer. Anything you entered is kept, ' +
      'so Claude can reopen it.');
    document.title = '\\u231b ' + BASE_TITLE;
    // Reopening depends on Claude doing it; the copy box works even if it never does.
    fallback({ kind: 'submit', data: collect() }, 'Claude Code stopped waiting for this answer',
      'Claude Code stopped waiting');
    if (reusable) startReusePolling();
  }

  function fallback(payload, reason, title) {
    // No navigator.clipboard.writeText() here — writing to the clipboard without the
    // user asking for it is a surprise, not a convenience. The textarea is selected
    // instead, so copying is still one keypress away, but it's the user's keypress.
    var box = document.getElementById('pp-fallback');
    var text = JSON.stringify(payload, null, 2);
    box.hidden = false;
    var textarea = box.querySelector('textarea');
    textarea.value = text;
    document.getElementById('pp-fallback-reason').textContent =
      reason + ' \\u2014 copy the text below and paste it to Claude Code.';
    document.getElementById('pp-fallback-title').textContent =
      title || "Couldn't reach Claude Code";
    textarea.select();
  }

  // Only meaningful on a reused tab (STATUS_URL set — see listener.py's
  // SessionListener): once this question is answered, keep checking whether the process
  // has published a new one, and reload to pick it up when it has. On the ordinary
  // one-shot page this never runs.
  function startReusePolling() {
    if (!STATUS_URL) return;
    var timer = setInterval(async function () {
      try {
        var res = await fetch(STATUS_URL);
        if (!res.ok) return;
        var body = await res.json();
        if (body.generation !== GENERATION) {
          clearInterval(timer);
          location.reload();
        }
      } catch (e) { /* process gone — nothing left to reload for */ }
    }, 1500);
  }

  async function send(kind, data) {
    var payload = { kind: kind, data: data };
    try {
      var res = await fetch(SUBMIT_URL, {
        method: 'POST',
        // text/plain keeps this a CORS simple request, so there is no preflight for a
        // file:// origin to fail.
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        // Branch on the status, not on whether fetch threw: a 403 from a stale token
        // resolves successfully and would otherwise be reported as sent.
        if (kind === 'submit' || kind === 'cancel' || kind === 'interrupt') {
          fallback(payload, 'Claude Code rejected the answer (HTTP ' + res.status + ')');
        }
        return false;
      }
      return true;
    } catch (err) {
      // An answer is worth copying out by hand; the background traffic only tells that
      // nobody is listening any more.
      if (kind === 'submit' || kind === 'cancel' || kind === 'interrupt') {
        fallback(payload, 'Claude Code is no longer listening (' + err.message + ')');
      } else expire(false);
      return false;
    }
  }

  // "You can close this tab" is only true on the ordinary one-shot page — a reused tab
  // (STATUS_URL set) is about to reload itself for the next question instead.
  function doneMessage(base) {
    return STATUS_URL
      ? base + ' Waiting for the next question\\u2026'
      : base + ' You can close this tab.';
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (stopping) { stopSend.click(); return; }
    if (submitBtn.disabled) return;
    var data = collect();
    freeze('Sending\\u2026');
    if (await send('submit', data)) {
      freeze(doneMessage('Answer sent.'));
      document.title = '\\u2705 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  cancelBtn.addEventListener('click', async function () {
    freeze('Cancelling\\u2026');
    if (await send('cancel', {})) {
      freeze(doneMessage('Cancelled.'));
      document.title = '\\u2716 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  document.addEventListener('keydown', function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  refresh();
  if (freeText) freeText.focus();
  else if (inputs.length) inputs[0].focus();
  // Proves the token and the CORS response are readable from a real page load before
  // the operator has spent any time answering. Never consumes the one submission.
  send('probe', {});
  startHeartbeat();
}());
</script>
__MERMAID__
</body>
</html>
"""


# A prototype sibling of _TEMPLATE for a page carrying several questions — see
# build_multi_page's docstring for why this is a separate template rather than a
# generalisation of the one above. A wizard: one `.pp-step` visible at a time (toggled by
# `.pp-step-active`, not by removing the others from the DOM, so every step's markup is
# still present for a static read of the page — see the page.py tests), a progress bar
# in place of the single-question page's one static question, and Back/Next replacing
# Send/Cancel until the last step, where Next becomes the actual submit.
_MULTI_TEMPLATE = """<!doctype html>
<html lang="en"__THEME_ATTR__>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
</style>
</head>
<body>
<main__MAIN_EXTRA__>
  <button id="pp-theme" type="button" aria-label="Toggle theme">&#9680;</button>

__HEADER__
  <div class="pp-progress">
    <span class="pp-progress-label" id="pp-progress-label">__INITIAL_LABEL__</span>
    <span class="pp-progress-track">
      <span class="pp-progress-fill" id="pp-progress-fill"
        style="width: __INITIAL_PROGRESS__%"></span>
    </span>
  </div>

  <form id="pp-form" novalidate>
__STEPS__
    <div class="pp-actions">
      <button type="button" id="pp-cancel">Cancel</button>
      <button type="button" id="pp-stop">Stop here</button>
      <button type="button" id="pp-stop-undo" hidden>never mind</button>
      <div class="pp-actions-end">
        <span id="pp-status"></span>
        <button type="button" id="pp-stop-send" hidden disabled>Stop and send</button>
        <button type="button" id="pp-back" disabled>Back</button>
        <button type="button" id="pp-next" disabled>__INITIAL_NEXT_LABEL__</button>
      </div>
    </div>
  </form>

  <div id="pp-fallback" hidden>
    <strong id="pp-fallback-title">Couldn't reach Claude Code</strong>
    <p data-role="body" id="pp-fallback-reason"></p>
    <textarea readonly rows="6"></textarea>
  </div>
</main>

<div class="pp-lightbox" id="pp-lightbox" hidden><img id="pp-lightbox-img" alt=""></div>

<script>
(function () {
  var SUBMIT_URL = __SUBMIT_URL__;
  var TIMEOUT_SECONDS = __TIMEOUT_SECONDS__;
  var STEP_COUNT = __STEP_COUNT__;
  var THEME_KEY = 'no-text-wall:theme';
  // So the tab bar shows whether this question is open or done without switching to the
  // tab — useful once several are open, or once one is answered and left to wait its
  // turn. The pending mark goes on right away; submit/cancel below replace it.
  var BASE_TITLE = document.title;
  document.title = '\\u2753 ' + BASE_TITLE;
  // Set only when this tab is reused across `ask` calls (see listener.py's
  // SessionListener) — null on the ordinary one-shot page, where startReusePolling()
  // below is then a no-op.
  var STATUS_URL = __STATUS_URL__;
  var GENERATION = __GENERATION__;
  var MERMAID_SRC = __MERMAID_SRC__;

  // Any image (a gallery thumbnail or one in an option/question body) expands full-size
  // on click — a thumbnail is otherwise too small to actually compare against another.
  var lightbox = document.getElementById('pp-lightbox');
  var lightboxImg = document.getElementById('pp-lightbox-img');
  function wireLightbox(img) {
    img.addEventListener('click', function (e) {
      e.stopPropagation();
      lightboxImg.src = img.src;
      lightboxImg.alt = img.alt;
      lightbox.hidden = false;
    });
  }
  document.querySelectorAll('main img').forEach(wireLightbox);
  lightbox.addEventListener('click', function () {
    lightbox.hidden = true;
    lightboxImg.src = '';
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !lightbox.hidden) {
      lightbox.hidden = true;
      lightboxImg.src = '';
    }
  });

  var form = document.getElementById('pp-form');
  var actions = form.querySelector('.pp-actions');
  var backBtn = document.getElementById('pp-back');
  var nextBtn = document.getElementById('pp-next');
  var cancelBtn = document.getElementById('pp-cancel');
  var status = document.getElementById('pp-status');
  var progressLabel = document.getElementById('pp-progress-label');
  var progressFill = document.getElementById('pp-progress-fill');
  var steps = [];
  var inputs = [];
  var drawers = [];
  var freeTexts = [];
  var stepDeferred = [];
  // `changed_from` and `visits` (in collect()): see the single-question page for
  // `picked`. Inputs, not ids, since two steps may reuse an option id. `visits` counts
  // showStep() calls per step, so the first view is 1 and each Back or Next onto it adds 1.
  var picked = [];
  var visits = [];
  var current = 0;
  // The furthest step shown so far, for the progress report a `resume` draft is built from.
  var furthest = 0;
  var frozen = false;

  // The rest of a drawer's row selects too, like an option without a body. Only the
  // Details pill opens the drawer, and the keyboard: Enter on the focused row arrives as
  // a click with detail 0.
  function wireSummary(s) {
    var input = s.querySelector('input');
    s.addEventListener('click', function (e) {
      if (e.detail === 0 || e.target.closest('.pp-option-details')) return;
      e.preventDefault();
      if (!input.disabled) input.click();
    });
  }

  // Everything one step needs, wired once for each step on the page at load.
  function wireStep(el) {
    steps.push(el);
    visits.push(0);
    el.querySelectorAll('input[data-option-id]').forEach(function (i) {
      inputs.push(i);
      i.addEventListener('change', function () {
        if (i.checked && picked.indexOf(i) === -1) picked.push(i);
        refresh();
      });
      // See the single-question page: a click on the checked radio clears it.
      if (i.type === 'radio') {
        i.addEventListener('click', function () {
          if (!i._wasChecked) return;
          i.checked = false;
          i.dispatchEvent(new Event('change'));
        });
      }
      // See page.py's _render_options: a long body wraps the input in <summary>, so a
      // click on the checkbox/radio would otherwise also toggle the drawer open.
      i.addEventListener('click', function (e) { e.stopPropagation(); });
    });
    // `expanded` (below, in collect()) is "was this ever opened", not "is it open right
    // now": with several long-bodied options, closing one after reading it to move to
    // the next is the expected motion, and a snapshot at submit time would read that as
    // never having been opened. The initial `open` attribute (recommended: true) counts
    // as having been shown too, seeded here rather than waiting for a first `toggle`.
    el.querySelectorAll('.pp-option-drawer[data-option-id]').forEach(function (d) {
      drawers.push(d);
      d._wasExpanded = d.open;
      d.addEventListener('toggle', function () { if (d.open) d._wasExpanded = true; });
    });
    el.querySelectorAll('.pp-option-drawer > summary').forEach(wireSummary);
    el.querySelectorAll('textarea[data-step]').forEach(function (t) {
      freeTexts.push(t);
      t.addEventListener('input', refresh);
    });
    stepDeferred.push(wireDefer(el, function () { refresh(); }));
  }

  // A reload on a reused tab (see STATUS_URL/startReusePolling below) always means a
  // genuinely new set of questions, never the ones just answered — but the browser
  // doesn't know that, and some (Firefox in particular) restore form field values across
  // a plain reload keyed by field identity/position. Since the new questions' fields can
  // occupy the same identity as the old ones, that restore has to be undone before
  // anything else reads these fields, or a new question can silently start pre-answered.
  document.querySelectorAll('input[data-option-id]').forEach(function (i) {
    i.checked = i.defaultChecked;
  });
  document.querySelectorAll('textarea[data-step]').forEach(function (t) {
    t.value = t.defaultValue;
  });
  document.querySelectorAll('.pp-step').forEach(wireStep);

  // The server already set the theme attribute from its own saved state whenever it has
  // one (see page.py's __THEME_ATTR__); localStorage is only the fallback for the very
  // first page it ever serves.
  if (!document.documentElement.dataset.theme) {
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
    } catch (e) { /* private mode: fall back to the OS preference */ }
  }
  document.getElementById('pp-theme').addEventListener('click', function () {
    var dark = getComputedStyle(document.body).backgroundColor === 'rgb(30, 30, 30)';
    var next = document.documentElement.dataset.theme
      ? (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
      : (dark ? 'light' : 'dark');
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* not persisted, still applied */ }
    // Fire-and-forget: the next page this process serves reads this back (see
    // page.py's read_saved_theme), so the preference survives past this one tab.
    send('theme', { theme: next });
  });

  function stepInputs(step) {
    return inputs.filter(function (i) { return i.dataset.step === String(step); });
  }
  // Drawers carry no data-step of their own — scoped by DOM ancestry (their step's
  // <section>) instead, the same way stepFreeText's id already ties a field to a step.
  function stepDrawers(step) {
    return Array.prototype.slice.call(
      steps[step].querySelectorAll('.pp-option-drawer[data-option-id]')
    );
  }
  function stepFreeText(step) {
    return document.getElementById('pp-free-text-' + step);
  }
  // Deferral (allow_defer): the "Not yet" pill locks the options it sits beside and
  // clears any selection, since "not yet, but B" is not an answer the agent can act on,
  // while the free-text box stays open for the reason. Returns a function reading the
  // current state, so collect() can report it. Only called when the pill exists.
  function wireDefer(root, onChange) {
    var btn = root.querySelector('.pp-defer');
    if (!btn) return function () { return false; };
    var banner = root.querySelector('.pp-defer-banner');
    var list = root.querySelector('.pp-options');
    var text = root.querySelector('textarea[id^="pp-free-text"]');
    var boxes = Array.prototype.slice.call(root.querySelectorAll('input[data-option-id]'));
    var placeholder = text ? text.placeholder : '';
    var on = false;
    btn.addEventListener('click', function () {
      on = !on;
      btn.setAttribute('aria-pressed', String(on));
      btn.textContent = on ? 'Deferred, undo' : 'Not yet';
      banner.hidden = !on;
      if (list) list.classList.toggle('pp-locked', on);
      boxes.forEach(function (i) {
        if (on) i.checked = false;
        i.disabled = on;
      });
      if (text) text.placeholder = on ? 'Why not yet? (optional)' : placeholder;
      onChange();
    });
    return function () { return on; };
  }

  function stepAnswered(step) {
    if (stepDeferred[step]()) return true;
    var selected = stepInputs(step).some(function (i) { return i.checked; });
    var ft = stepFreeText(step);
    return selected || (ft && ft.value.trim() !== '');
  }
  function collect() {
    var answers = [];
    for (var s = 0; s < STEP_COUNT; s++) {
      var ft = stepFreeText(s);
      answers.push({
        selected: stepInputs(s).filter(function (i) { return i.checked; })
                                .map(function (i) { return i.dataset.optionId; }),
        free_text: ft ? ft.value.trim() : '',
        expanded: stepDrawers(s).filter(function (d) { return d._wasExpanded; })
                                 .map(function (d) { return d.dataset.optionId; }),
        changed_from: picked.filter(function (i) { return i.dataset.step === String(s); })
                            .map(function (i) { return i.dataset.optionId; }),
        visits: visits[s],
        deferred: stepDeferred[s]()
      });
    }
    return { answers: answers };
  }

  function showStep(index) {
    visits[index] += 1;
    furthest = Math.max(furthest, index);
    steps.forEach(function (el, i) { el.classList.toggle('pp-step-active', i === index); });
    backBtn.disabled = index === 0;
    refresh();
  }

  // Only the current step's answer gates Next — earlier steps were already checked on
  // their own way past, and later ones haven't been seen yet.
  function refresh() {
    inputs.forEach(function (i) { i._wasChecked = i.checked; });
    // After Send or Cancel: a step arriving late must not re-enable anything.
    if (frozen) return;
    var last = current === STEP_COUNT - 1;
    progressLabel.textContent = 'Question ' + (current + 1) + ' of ' + STEP_COUNT;
    progressFill.style.width = ((current + 1) / STEP_COUNT * 100) + '%';
    nextBtn.textContent = last ? 'Send answers' : 'Next';
    nextBtn.disabled = !stepAnswered(current);
    reportProgress();
  }

  // Reported so an ask can be resumed with these answers prefilled if it times out or
  // is closed. Debounced: refresh() runs on every keystroke in a free-text box.
  var progressTimer = null;
  function reportProgress() {
    clearTimeout(progressTimer);
    progressTimer = setTimeout(function () {
      send('progress', { answers: collect().answers.slice(0, furthest + 1) });
    }, 400);
  }

  // Loaded on first use: a step with a diagram can arrive after the page, which only
  // loads mermaid itself when it had one from the start (see _mermaid_script).
  function renderMermaid(nodes) {
    if (!nodes.length) return;
    function run() { nodes.forEach(function (el) { mermaid.run({ nodes: [el] }); }); }
    if (window.mermaid) { run(); return; }
    var script = document.createElement('script');
    script.src = MERMAID_SRC;
    script.onload = function () {
      var dark = document.documentElement.dataset.theme
        ? document.documentElement.dataset.theme === 'dark'
        : matchMedia('(prefers-color-scheme: dark)').matches;
      mermaid.initialize({
        startOnLoad: false, theme: dark ? 'dark' : 'default', securityLevel: 'strict'
      });
      run();
    };
    document.head.appendChild(script);
  }

  // The page's side of the shared Interruption script below.
  function stopTarget() {
    return { root: steps[current], before: null, id: 'pp-free-text-' + current, index: current };
  }
  function stopMode(on) {
    nextBtn.hidden = on;
    backBtn.disabled = on || current === 0;
  }
  function stopPayload(stop) {
    var data = collect();
    data.interrupted = stop.index;
    return data;
  }
  // Interruption ("Stop here", see CONTEXT.md): ends the ask at once with the answers so
  // far and the current question marked, so the agent reads it before anything else. It
  // needs the user's own words there: the question's text box turns required, and one is
  // added where the question has none (allow_free_text off). stopTarget, stopMode and
  // stopPayload above are each page's side of it.
  var stopBtn = document.getElementById('pp-stop');
  var stopSend = document.getElementById('pp-stop-send');
  var stopUndo = document.getElementById('pp-stop-undo');
  var stopping = null;
  function syncStop() {
    stopSend.disabled = frozen || !stopping || stopping.box.value.trim() === '';
  }
  stopBtn.addEventListener('click', function () {
    var target = stopTarget();
    var box = document.getElementById(target.id);
    var made = !box;
    if (made) {
      var holder = document.createElement('label');
      holder.className = 'pp-free-text';
      holder.appendChild(document.createTextNode(''));
      box = document.createElement('textarea');
      box.id = target.id;
      box.rows = 3;
      holder.appendChild(box);
      target.root.insertBefore(holder, target.before);
    }
    var label = box.parentNode;
    stopping = { box: box, made: made, index: target.index, label: label.firstChild.nodeValue };
    label.firstChild.nodeValue = 'Why stop here? Claude reads this first.';
    label.classList.add('pp-required');
    box.addEventListener('input', syncStop);
    stopBtn.hidden = true;
    stopSend.hidden = false;
    stopUndo.hidden = false;
    stopMode(true);
    syncStop();
    box.focus();
  });
  stopUndo.addEventListener('click', function () {
    var label = stopping.box.parentNode;
    stopping.box.removeEventListener('input', syncStop);
    if (stopping.made) label.remove();
    else {
      label.classList.remove('pp-required');
      label.firstChild.nodeValue = stopping.label;
    }
    stopping = null;
    stopBtn.hidden = false;
    stopSend.hidden = true;
    stopUndo.hidden = true;
    stopMode(false);
  });
  stopSend.addEventListener('click', async function () {
    if (stopSend.disabled) return;
    var data = stopPayload(stopping);
    freeze('Sending\\u2026');
    if (await send('interrupt', data)) {
      freeze(doneMessage('Stopped here. Claude reads your note first.'));
      document.title = '\\u270b ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });
  function freezeStop() {
    if (stopping) stopping.box.readOnly = true;
    stopBtn.disabled = true;
    stopSend.disabled = true;
    stopUndo.hidden = true;
  }

  function freeze(message) {
    frozen = true;
    inputs.forEach(function (i) { i.disabled = true; });
    document.querySelectorAll('.pp-defer').forEach(function (b) { b.disabled = true; });
    freeTexts.forEach(function (t) { t.readOnly = true; });
    freezeStop();
    backBtn.disabled = true;
    nextBtn.disabled = true;
    cancelBtn.disabled = true;
    status.hidden = false;
    status.textContent = message;
    stopHeartbeat();
  }

  var HEARTBEAT_MS = Math.min(60000, Math.max(2000, (TIMEOUT_SECONDS * 1000) / 4));
  var heartbeatTimer = null;
  // On a reused tab GET /status keeps the call alive the same way a probe does, and
  // also says whether a call still waits on this page. A one-shot page learns that its
  // call ended from the probe failing, since its listener closes with the call.
  async function heartbeat() {
    if (document.visibilityState !== 'visible') return;
    if (!STATUS_URL) { send('probe', {}); return; }
    try {
      var res = await fetch(STATUS_URL);
      if (!res.ok) return;
      var body = await res.json();
      if (body.generation !== GENERATION) expire(false);
      else if (!body.open) expire(true);
    } catch (e) { expire(false); }
  }
  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(heartbeat, HEARTBEAT_MS);
  }
  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') heartbeat();
  });

  // The call this page answers ended without an answer: it timed out while the tab was
  // hidden, or the server is gone. Freezing beats taking an answer nobody will read. On a
  // reused tab still showing its own generation, the next question can still land here.
  function expire(reusable) {
    if (frozen) return;
    freeze('Claude Code stopped waiting for this answer. Anything you entered is kept, ' +
      'so Claude can reopen it.');
    document.title = '\\u231b ' + BASE_TITLE;
    // Reopening depends on Claude doing it; the copy box works even if it never does.
    fallback({ kind: 'submit', data: collect() }, 'Claude Code stopped waiting for this answer',
      'Claude Code stopped waiting');
    if (reusable) startReusePolling();
  }

  function fallback(payload, reason, title) {
    var box = document.getElementById('pp-fallback');
    var text = JSON.stringify(payload, null, 2);
    box.hidden = false;
    var textarea = box.querySelector('textarea');
    textarea.value = text;
    document.getElementById('pp-fallback-reason').textContent =
      reason + ' \\u2014 copy the text below and paste it to Claude Code.';
    document.getElementById('pp-fallback-title').textContent =
      title || "Couldn't reach Claude Code";
    textarea.select();
  }

  // Only meaningful on a reused tab (STATUS_URL set — see listener.py's
  // SessionListener): once this question is answered, keep checking whether the process
  // has published a new one, and reload to pick it up when it has. On the ordinary
  // one-shot page this never runs.
  function startReusePolling() {
    if (!STATUS_URL) return;
    var timer = setInterval(async function () {
      try {
        var res = await fetch(STATUS_URL);
        if (!res.ok) return;
        var body = await res.json();
        if (body.generation !== GENERATION) {
          clearInterval(timer);
          location.reload();
        }
      } catch (e) { /* process gone — nothing left to reload for */ }
    }, 1500);
  }

  async function send(kind, data) {
    var payload = { kind: kind, data: data };
    try {
      var res = await fetch(SUBMIT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        if (kind === 'submit' || kind === 'cancel' || kind === 'interrupt') {
          fallback(payload, 'Claude Code rejected the answer (HTTP ' + res.status + ')');
        }
        return false;
      }
      return true;
    } catch (err) {
      if (kind === 'submit' || kind === 'cancel' || kind === 'interrupt') {
        fallback(payload, 'Claude Code is no longer listening (' + err.message + ')');
      } else expire(false);
      return false;
    }
  }

  backBtn.addEventListener('click', function () {
    if (current > 0) { current -= 1; showStep(current); }
  });

  // "You can close this tab" is only true on the ordinary one-shot page — a reused tab
  // (STATUS_URL set) is about to reload itself for the next question instead.
  function doneMessage(base) {
    return STATUS_URL
      ? base + ' Waiting for the next question\\u2026'
      : base + ' You can close this tab.';
  }

  nextBtn.addEventListener('click', async function () {
    if (nextBtn.disabled) return;
    if (current < STEP_COUNT - 1) {
      current += 1;
      showStep(current);
      return;
    }
    var data = collect();
    freeze('Sending\\u2026');
    if (await send('submit', data)) {
      freeze(doneMessage('Answers sent.'));
      document.title = '\\u2705 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  cancelBtn.addEventListener('click', async function () {
    freeze('Cancelling\\u2026');
    if (await send('cancel', {})) {
      freeze(doneMessage('Cancelled.'));
      document.title = '\\u2716 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  document.addEventListener('keydown', function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      (stopping ? stopSend : nextBtn).click();
    }
  });

  showStep(0);
  if (inputs.length) inputs[0].focus();
  else if (freeTexts.length) freeTexts[0].focus();
  // Proves the token and the CORS response are readable from a real page load before
  // the operator has spent any time answering. Never consumes the one submission.
  send('probe', {});
  startHeartbeat();
}());
</script>
__MERMAID__
</body>
</html>
"""


# Sibling of _ADDENDUM for `show`: same tokens and font, but no option/free-text rules —
# `show` has no choices to render, only content plus the Save/Acknowledge actions below.
# Kept as its own block rather than a subset of _ADDENDUM so `show`'s CSS doesn't carry
# rules for elements it never renders.
_SHOW_ADDENDUM = """
* { box-sizing: border-box; }
:root {
  --pp-font-mono: "JetBrains Mono", ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  --ntw-bg: #fafaf9;
  --ntw-bg-elevated: #ffffff;
  --ntw-fg: #0a0a0a;
  --ntw-fg-muted: #54595f;
  --ntw-border: #c7cbd1;
  --ntw-accent: #1f7a4c;
  --ntw-accent-contrast: #ffffff;
  --ntw-verdict-bg: #e3f3ea;
  --ntw-code-bg: #f0f0f0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ntw-bg: #1e1e1e;
    --ntw-bg-elevated: #262626;
    --ntw-fg: #e6e6e6;
    --ntw-fg-muted: #9d9d9d;
    --ntw-border: #3f3f3f;
    --ntw-accent: #3ddc84;
    --ntw-accent-contrast: #00150a;
    --ntw-verdict-bg: #16281f;
    --ntw-code-bg: #2a2a2a;
  }
}
:root[data-theme="dark"] {
  --ntw-bg: #1e1e1e;
  --ntw-bg-elevated: #262626;
  --ntw-fg: #e6e6e6;
  --ntw-fg-muted: #9d9d9d;
  --ntw-border: #3f3f3f;
  --ntw-accent: #3ddc84;
  --ntw-accent-contrast: #00150a;
  --ntw-verdict-bg: #16281f;
  --ntw-code-bg: #2a2a2a;
}
html { font-size: 18px; }
body { margin: 0; padding: 20px 16px 40px; }
main { max-width: 680px; margin: 0 auto; position: relative; }
/* Same value and trigger condition as _ADDENDUM's main.pp-wide (_needs_wide_layout) —
   a ```diff/code fence or a table hits its own horizontal scrollbar well before an
   ordinary report would, at this width. */
main.pp-wide { max-width: 920px; }

code { font-family: var(--pp-font-mono); font-size: 0.88em; background: var(--ntw-code-bg);
  padding: 0.1em 0.35em; border-radius: 4px; }
pre.code-block { font-family: var(--pp-font-mono); }
pre.code-block code { background: none; padding: 0; border-radius: 0; }

/* Same reasoning as _ADDENDUM's pre.mermaid/pre.mermaid svg — see there. */
pre.mermaid { margin: 0.6em 0; text-align: center; white-space: normal;
  font-family: inherit; }
pre.mermaid svg { max-width: 100%; height: auto; }

blockquote { border-inline-start: 4px solid var(--ntw-border); color: var(--ntw-fg-muted);
  margin: 0.6em 0; padding-inline-start: 14px; }
blockquote > p:first-child { margin-block-start: 0; }
blockquote > p:last-child { margin-block-end: 0; }

table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 0.9em; }
th, td { padding: 6px 10px; text-align: left; border: none;
  border-bottom: 1px solid var(--ntw-border); }
thead th { border-bottom: 2px solid var(--ntw-accent); color: var(--ntw-accent);
  font-weight: 600; text-transform: uppercase; font-size: 0.78em; letter-spacing: 0.03em; }
tbody tr:last-child td { border-bottom: none; }

main img { cursor: zoom-in; max-width: 100%; height: auto; border-radius: 6px; }
.pp-lightbox { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); z-index: 1000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
.pp-lightbox[hidden] { display: none; }
.pp-lightbox img { max-width: 92vw; max-height: 92vh; border-radius: 6px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5); }

#pp-theme { position: absolute; top: -4px; right: 0; width: 30px; height: 30px; border: none;
  background: transparent; color: var(--ntw-fg-muted); border-radius: 999px; cursor: pointer;
  font-size: 15px; line-height: 1; display: flex; align-items: center; justify-content: center; }
#pp-theme:hover { background: var(--ntw-border); color: var(--ntw-fg); }

.pp-content > :first-child { margin-block-start: 0; }
.pp-content > :last-child { margin-block-end: 0; }

/* Same visual language as _MULTI_ADDENDUM's .pp-progress (ask's wizard), duplicated
   rather than shared for the same reason _SHOW_ADDENDUM as a whole is its own block —
   see the module docstring. Deck's equivalent of that wizard's per-question progress. */
/* 40px right margin, not 0: clears #pp-theme (absolute, top-right) the same way
   _MULTI_ADDENDUM's original .pp-progress already does — dropping it earlier caused
   a real overlap with the theme toggle, caught in review. */
.pp-progress { display: flex; align-items: center; gap: 10px; margin: 0 40px 16px 0; }
.pp-progress-label { font-size: 0.8em; color: var(--ntw-fg-muted); white-space: nowrap; }
.pp-progress-track { flex: 1; height: 4px; border-radius: 999px; background: var(--ntw-border);
  overflow: hidden; }
.pp-progress-fill { height: 100%; background: var(--ntw-accent); border-radius: 999px;
  transition: width .15s; }

.pp-actions { margin-block-start: 22px; }

.pp-save-menu { position: absolute; top: 100%; left: 0; margin-block-start: 6px;
  display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--ntw-border);
  border-radius: 6px; background: var(--ntw-bg-elevated); padding: 4px; z-index: 10;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15); }
.pp-save-menu[hidden] { display: none; }
.pp-save-menu button { border: none; background: none; text-align: left;
  white-space: nowrap; }
.pp-save-menu button:hover { background: var(--ntw-border); }

/* Same reasoning and colours as _ADDENDUM's #pp-fallback — see there. No textarea here:
   an acknowledgement that fails to send loses nothing the way a lost answer would for
   `ask`, since the report itself is still on screen. */
#pp-fallback { border: 1px solid var(--ntw-danger); border-radius: 6px;
  background: #fef2f2; padding: 12px 14px; margin-block-start: 12px; }
#pp-fallback strong { color: var(--ntw-danger); }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) #pp-fallback { background: #3f1d1d; }
}
:root[data-theme="dark"] #pp-fallback { background: #3f1d1d; }
"""

# `show` gets a listener-driven submit script only when a call actually wires one up
# (see page.py's build_show_page and show.py's run_show) — the fallback box, heartbeat
# and reuse-polling are copied from _TEMPLATE's script since a listener-backed show page
# needs the exact same idle-timeout and tab-reuse behaviour ask's page does. Save is pure
# client-side Blob download and needs none of that, whether or not a listener exists.
_SHOW_TEMPLATE = """<!doctype html>
<html lang="en"__THEME_ATTR__>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
</style>
</head>
<body>
<main__MAIN_EXTRA__>
  <button id="pp-theme" type="button" aria-label="Toggle theme">&#9680;</button>
  <div class="pp-content">
__CONTENT__
  </div>

  <div class="pp-actions">
__SAVE_BUTTON__
    <div class="pp-actions-end">
      <span id="pp-status"></span>
__ACK_BUTTON__
    </div>
  </div>

  <div id="pp-fallback" hidden>
    <strong id="pp-fallback-title">Couldn't reach Claude Code</strong>
    <p data-role="body" id="pp-fallback-reason"></p>
  </div>
</main>

<div class="pp-lightbox" id="pp-lightbox" hidden><img id="pp-lightbox-img" alt=""></div>

<script>
(function () {
  var SUBMIT_URL = __SUBMIT_URL__;
  var TIMEOUT_SECONDS = __TIMEOUT_SECONDS__;
  var STATUS_URL = __STATUS_URL__;
  var GENERATION = __GENERATION__;
  var RAW_CONTENT = __RAW_CONTENT__;
  var FILENAME_BASE = __FILENAME_BASE__;
  var THEME_KEY = 'no-text-wall:theme';
  var BASE_TITLE = document.title;

  var lightbox = document.getElementById('pp-lightbox');
  var lightboxImg = document.getElementById('pp-lightbox-img');
  document.querySelectorAll('main img').forEach(function (img) {
    img.addEventListener('click', function (e) {
      e.stopPropagation();
      lightboxImg.src = img.src;
      lightboxImg.alt = img.alt;
      lightbox.hidden = false;
    });
  });
  lightbox.addEventListener('click', function () {
    lightbox.hidden = true;
    lightboxImg.src = '';
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !lightbox.hidden) {
      lightbox.hidden = true;
      lightboxImg.src = '';
    }
  });

  if (!document.documentElement.dataset.theme) {
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
    } catch (e) { /* private mode: fall back to the OS preference */ }
  }
  document.getElementById('pp-theme').addEventListener('click', function () {
    var dark = getComputedStyle(document.body).backgroundColor === 'rgb(30, 30, 30)';
    var next = document.documentElement.dataset.theme
      ? (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
      : (dark ? 'light' : 'dark');
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* not persisted, still applied */ }
  });

  // Client-side only — a save never touches SUBMIT_URL, so it works exactly the same
  // whether or not this show call is being waited on. Markdown saves RAW_CONTENT (the
  // Markdown the agent actually passed, the portable/diffable record — see no-text-wall's
  // "HTML is a view, never the record"); HTML saves this page's own rendered markup, a
  // snapshot of what was on screen, tables/diff formatting and all.
  function download(filename, mime, data) {
    var blob = new Blob([data], { type: mime });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  var saveBtn = document.getElementById('pp-save');
  var saveMenu = document.getElementById('pp-save-menu');
  if (saveBtn && saveMenu) {
    saveBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      saveMenu.hidden = !saveMenu.hidden;
    });
    document.addEventListener('click', function () { saveMenu.hidden = true; });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') saveMenu.hidden = true;
    });
    document.getElementById('pp-save-md').addEventListener('click', function () {
      download(FILENAME_BASE + '.md', 'text/markdown', RAW_CONTENT);
      saveMenu.hidden = true;
    });
    document.getElementById('pp-save-html').addEventListener('click', function () {
      // A static snapshot of the content, not a serialization of this live page: the
      // theme toggle, Save/Acknowledge actions (and the save menu itself, open right
      // now) and the scripts that drive them have no meaning once this is a plain file
      // on disk, so they're stripped rather than saved half-working.
      var clone = document.documentElement.cloneNode(true);
      ['#pp-theme', '.pp-actions', '#pp-fallback', '.pp-lightbox', 'script'].forEach(
        function (selector) {
          clone.querySelectorAll(selector).forEach(function (el) { el.remove(); });
        }
      );
      download(FILENAME_BASE + '.html', 'text/html', '<!doctype html>\\n' + clone.outerHTML);
      saveMenu.hidden = true;
    });
  }

  var ackBtn = document.getElementById('pp-ack');
  var status = document.getElementById('pp-status');

  function fallback(payload, reason) {
    var box = document.getElementById('pp-fallback');
    box.hidden = false;
    document.getElementById('pp-fallback-reason').textContent =
      reason + ' \\u2014 the report is still on screen; only the acknowledgement failed to send.';
  }

  var HEARTBEAT_MS = Math.min(60000, Math.max(2000, (TIMEOUT_SECONDS * 1000) / 4));
  var heartbeatTimer = null;
  function heartbeat() {
    if (document.visibilityState === 'visible') send('probe', {});
  }
  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(heartbeat, HEARTBEAT_MS);
  }
  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') heartbeat();
  });

  function startReusePolling() {
    if (!STATUS_URL) return;
    var timer = setInterval(async function () {
      try {
        var res = await fetch(STATUS_URL);
        if (!res.ok) return;
        var body = await res.json();
        if (body.generation !== GENERATION) {
          clearInterval(timer);
          location.reload();
        }
      } catch (e) { /* process gone — nothing left to reload for */ }
    }, 1500);
  }

  async function send(kind, data) {
    var payload = { kind: kind, data: data };
    try {
      var res = await fetch(SUBMIT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        if (kind !== 'probe') {
          fallback(payload, 'Claude Code rejected it (HTTP ' + res.status + ')');
        }
        return false;
      }
      return true;
    } catch (err) {
      if (kind !== 'probe') {
        fallback(payload, 'Claude Code is no longer listening (' + err.message + ')');
      }
      return false;
    }
  }

  function doneMessage(base) {
    return STATUS_URL ? base + ' Waiting for the next thing\\u2026' : base;
  }

  if (ackBtn) {
    ackBtn.addEventListener('click', async function () {
      ackBtn.disabled = true;
      if (!SUBMIT_URL) {
        // No listener behind this page (see show.py's run_show): nothing to send,
        // acknowledging is purely a local mark that the report was seen.
        status.textContent = 'Acknowledged.';
        return;
      }
      status.textContent = 'Sending\\u2026';
      if (await send('acknowledge', {})) {
        status.textContent = doneMessage('Acknowledged.');
        document.title = '\\u2705 ' + BASE_TITLE;
        stopHeartbeat();
      } else {
        ackBtn.disabled = false;
        status.textContent = 'Not sent.';
      }
    });
  }

  if (SUBMIT_URL) {
    // Proves the token and CORS response are readable before the user has done
    // anything, same as ask's page — never consumes the one acknowledgement.
    send('probe', {});
    startHeartbeat();
  }
  // Unlike ask's page (which only starts this once its one answer is in, so a reload
  // can't sweep away an answer mid-composition), show has no answer to protect: it
  // polls from the moment it loads, so a later show/ask call reusing this tab lands
  // here as a live update instead of being silently overwritten on the server while
  // this page keeps showing stale content.
  startReusePolling();
}());
</script>
__DECK_SCRIPT__
__MERMAID__
</body>
</html>
"""


def _filename_base(title: str) -> str:
    """A filesystem-safe stem for the saved file, derived from `title`."""
    stem = "".join(c if c.isalnum() else "-" for c in title.strip().lower())
    stem = "-".join(filter(None, stem.split("-")))
    return stem or "report"


def _render_deck(
    slides: list[str],
    header: str | None,
    footer: str | None,
    asset_base_url: str | None = None,
) -> tuple[str, str]:
    """Return (content_html, raw_markdown) for a deck-mode `show` call.

    Each of `header`/`footer`/every entry of `slides` is its own independent Markdown
    render — no shared state between them beyond which slide `_deck_script` currently
    shows. `raw_markdown` reconstructs one document for the Markdown Save button, joining
    slides on a `---` line; that line is never parsed back (there is no deck-from-Markdown
    path), it only has to read sensibly as a saved file.
    """
    header_html = render_markdown(header, asset_base_url=asset_base_url) if header else ""
    footer_html = render_markdown(footer, asset_base_url=asset_base_url) if footer else ""
    total = len(slides)
    initial_progress = round(100 / total) if total else 0
    progress_html = (
        '<div class="pp-progress">'
        f'<span class="pp-progress-label" id="pp-deck-progress-label">Slide 1 of {total}</span>'
        '<span class="pp-progress-track">'
        f'<span class="pp-progress-fill" id="pp-deck-progress-fill" '
        f'style="width: {initial_progress}%"></span>'
        "</span>"
        "</div>"
    )
    slide_parts = []
    for index, slide in enumerate(slides):
        active = " is-active" if index == 0 else ""
        slide_html = render_markdown(slide, asset_base_url=asset_base_url)
        slide_parts.append(f'<section class="deck-slide card{active}">{slide_html}</section>')
    nav_html = (
        '<div class="deck-nav">'
        '<button type="button" data-deck-prev aria-label="Previous slide">&larr;</button>'
        '<button type="button" data-deck-next aria-label="Next slide">&rarr;</button>'
        "</div>"
    )
    content_html = header_html + progress_html + "\n".join(slide_parts) + nav_html + footer_html

    raw_parts = [part for part in (header, "\n\n---\n\n".join(slides), footer) if part]
    return content_html, "\n\n".join(raw_parts)


def build_show_page(
    *,
    content: str = "",
    slides: list[str] | None = None,
    header: str | None = None,
    footer: str | None = None,
    title: str = "Claude Code",
    accent: str | None = None,
    theme: str | None = None,
    save_button: bool = True,
    acknowledge_button: bool = True,
    submit_url: str | None = None,
    timeout_seconds: float = 300.0,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    """Return the complete HTML document for one `show` call.

    `content` is Markdown, rendered the same way `question` is for `ask` — or, in deck
    mode, `slides` is a list of independent Markdown documents shown one at a time with
    Prev/Next/arrow-key nav, optionally framed by a `header`/`footer` shown on every
    slide. `content` and `slides` are mutually exclusive, validated by `show.ShowRequest`
    before this is called. Save is always client-side (a Blob download), regardless of
    `submit_url`. Acknowledge only actually reaches the agent when `submit_url` is set —
    see `show.run_show` for when that is: the shared session (always available) or, on
    the one-off fallback tab, only if the call opted into waiting for it. With no
    `submit_url`, the Acknowledge button still shows and responds, but purely as a local
    "seen" mark, never sent anywhere.

    `submit_url`/`status_url`/`generation`/`timeout_seconds` mirror `build_page`'s: set
    together when this page is served by a `SessionListener` or a one-shot
    `LoopbackListener`, left at their defaults for a plain unlistened page.
    """
    css = _load_base_css() + _ACTIONS_CSS + _SHOW_ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    save_html = ""
    if save_button:
        save_html = (
            '    <button type="button" id="pp-save">Save report</button>\n'
            '    <div class="pp-save-menu" id="pp-save-menu" hidden>\n'
            '      <button type="button" id="pp-save-md">as Markdown (.md)</button>\n'
            '      <button type="button" id="pp-save-html">as HTML (.html)</button>\n'
            "    </div>"
        )
    ack_html = ""
    if acknowledge_button:
        ack_html = '      <button type="button" id="pp-ack">Acknowledge</button>'

    asset_base_url = _asset_base_url(status_url)
    if slides is not None:
        content_html, raw_content = _render_deck(slides, header, footer, asset_base_url)
    else:
        content_html = render_markdown(content, asset_base_url=asset_base_url)
        raw_content = content
    wide = _needs_wide_layout(content_html)

    return (
        _SHOW_TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__MAIN_EXTRA__", ' class="pp-wide"' if wide else "")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__CONTENT__", content_html)
        .replace("__DECK_SCRIPT__", _deck_script(slides is not None))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(content_html)))
        .replace("__SAVE_BUTTON__", save_html)
        .replace("__ACK_BUTTON__", ack_html)
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__RAW_CONTENT__", _js_string(raw_content))
        .replace("__FILENAME_BASE__", _js_string(_filename_base(title)))
    )


# A `review` call's own compact components, same reasoning as _SHOW_ADDENDUM/_ADDENDUM
# for why this duplicates rather than shares their palette/font/actions/fallback rules —
# see the module docstring. The new pieces here (from `.pp-review-path` down) are its
# own: a whole-row comment target per addressable diff line, and the overall-note box —
# see CONTEXT.md's "Review" section. The row/box styling is the synthesis chosen in a
# three-variant review (see pretty-prompt's CLAUDE.md): whole-row click to open, a
# decorative bubble glyph, and a callout-shaped box.
_REVIEW_ADDENDUM = """
* { box-sizing: border-box; }
:root {
  --pp-font-mono: "JetBrains Mono", ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  --ntw-bg: #fafaf9;
  --ntw-bg-elevated: #ffffff;
  --ntw-fg: #0a0a0a;
  --ntw-fg-muted: #54595f;
  --ntw-border: #c7cbd1;
  --ntw-accent: #1f7a4c;
  --ntw-accent-contrast: #ffffff;
  --ntw-verdict-bg: #e3f3ea;
  --ntw-code-bg: #f0f0f0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ntw-bg: #1e1e1e;
    --ntw-bg-elevated: #262626;
    --ntw-fg: #e6e6e6;
    --ntw-fg-muted: #9d9d9d;
    --ntw-border: #3f3f3f;
    --ntw-accent: #3ddc84;
    --ntw-accent-contrast: #00150a;
    --ntw-verdict-bg: #16281f;
    --ntw-code-bg: #2a2a2a;
  }
}
:root[data-theme="dark"] {
  --ntw-bg: #1e1e1e;
  --ntw-bg-elevated: #262626;
  --ntw-fg: #e6e6e6;
  --ntw-fg-muted: #9d9d9d;
  --ntw-border: #3f3f3f;
  --ntw-accent: #3ddc84;
  --ntw-accent-contrast: #00150a;
  --ntw-verdict-bg: #16281f;
  --ntw-code-bg: #2a2a2a;
}
html { font-size: 18px; }
/* 8px, not _ADDENDUM/_SHOW_ADDENDUM's 16px: a diff line is often wider than an ask
   question or show report, so review gives it back the few extra px of width before
   falling into .pp-review-diff's own horizontal scroll — caught by Operator hitting it
   on an ordinary function-signature-length line. */
body { margin: 0; padding: 20px 8px 40px; }
/* A fixed 760px (_ADDENDUM/_SHOW_ADDENDUM's shape) left most of a normal window unused
   while a diff line still hit .pp-review-diff's horizontal scroll — the real ceiling was
   this, not the body padding above. min(1200px, 96vw) instead: still capped so it
   doesn't stretch unreadably wide on an ultrawide monitor, but scales with the window on
   anything smaller instead of a fixed number picked for prose width. */
main { max-width: min(1200px, 96vw); margin: 0 auto; position: relative; }

#pp-theme { position: absolute; top: -4px; right: 0; width: 30px; height: 30px; border: none;
  background: transparent; color: var(--ntw-fg-muted); border-radius: 999px; cursor: pointer;
  font-size: 15px; line-height: 1; display: flex; align-items: center; justify-content: center; }
#pp-theme:hover { background: var(--ntw-border); color: var(--ntw-fg); }

.pp-content > :first-child { margin-block-start: 0; }
.pp-content > :last-child { margin-block-end: 0; }

.pp-progress { display: flex; align-items: center; gap: 10px; margin: 0 40px 16px 0; }
.pp-progress-label { font-size: 0.8em; color: var(--ntw-fg-muted); white-space: nowrap; }
.pp-progress-track { flex: 1; height: 4px; border-radius: 999px; background: var(--ntw-border);
  overflow: hidden; }
.pp-progress-fill { height: 100%; background: var(--ntw-accent); border-radius: 999px;
  transition: width .15s; }

.pp-review-path { font-family: var(--pp-font-mono); font-size: 0.82em; color: var(--ntw-fg-muted);
  margin-block-end: 8px; word-break: break-all; }
.pp-review-diff { font-family: var(--pp-font-mono); font-size: 0.85em; margin: 0.6em 0;
  border: 1px solid var(--ntw-border); border-radius: 6px; overflow: hidden; overflow-x: auto; }
.pp-review-row.hunk { color: var(--ntw-fg-muted); padding: 2px 10px;
  background: var(--ntw-code-bg); }
/* Whole row is the click/keyboard target — chosen over a separate gutter button in a
   three-variant review (see pretty-prompt's CLAUDE.md): nothing small to aim for, and the
   bubble glyph below is purely decorative, not itself a click target
   (`pointer-events: none`). The box that opens reads as a callout pointing back at the
   line, picked in that same review over a plain panel. */
.pp-review-row-wrap { position: relative; cursor: pointer; }
/* Once Submit/Cancel has been sent (body.pp-review-frozen, set by the script's freeze())
   a row no longer looks clickable — the comments it would collect have already left. */
body.pp-review-frozen .pp-review-row-wrap { cursor: default; }
.pp-review-row { display: flex; align-items: flex-start; padding: 1px 10px 1px 2px;
  white-space: pre; border-left: 3px solid transparent; position: relative; }
.pp-review-row.add { border-left-color: #1f7a4c; background: rgba(31, 122, 76, 0.08); }
.pp-review-row.del { border-left-color: #b3261e; background: rgba(179, 38, 30, 0.06);
  color: var(--ntw-fg-muted); }
.pp-review-row-wrap:hover .pp-review-row,
.pp-review-row-wrap:focus-within .pp-review-row { background: var(--ntw-border); }
body.pp-review-frozen .pp-review-row-wrap:hover .pp-review-row,
body.pp-review-frozen .pp-review-row-wrap:focus-within .pp-review-row { background: none; }
.pp-review-comment-icon { position: absolute; right: 8px; top: 1px; font-size: 0.85em;
  opacity: 0; color: var(--ntw-fg-muted); pointer-events: none; }
.pp-review-row-wrap:hover .pp-review-comment-icon,
.pp-review-row-wrap.has-comment .pp-review-comment-icon { opacity: 1; }
.pp-review-row-wrap.has-comment .pp-review-comment-icon { color: var(--ntw-accent); }
.pp-review-gutter { flex: none; width: 2.8em; text-align: right; padding-inline-end: 8px;
  color: var(--ntw-fg-muted); user-select: none; }
.pp-review-comment-box { display: none; position: relative; margin: 6px 10px 14px 46px;
  padding: 8px 10px; border-radius: 8px; border: 1px solid var(--ntw-accent);
  background: var(--ntw-verdict-bg); }
.pp-review-comment-box::before { content: ""; position: absolute; top: -7px; right: 24px;
  width: 12px; height: 12px; background: var(--ntw-verdict-bg);
  border-inline-start: 1px solid var(--ntw-accent);
  border-block-start: 1px solid var(--ntw-accent); transform: rotate(45deg); }
.pp-review-comment-box.is-open { display: block; }
.pp-review-comment-box textarea { width: 100%; min-height: 2.4em; font: inherit;
  font-size: 0.85em; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--ntw-border);
  background: var(--ntw-bg-elevated); color: var(--ntw-fg); resize: vertical; }

#pp-overall-note-label { font-size: 0.8em; color: var(--ntw-fg-muted); display: block;
  margin: 18px 0 4px; }
#pp-overall-note { width: 100%; min-height: 4em; font: inherit; font-size: 0.9em;
  padding: 8px 10px; border-radius: 6px; border: 1px solid var(--ntw-border);
  background: var(--ntw-bg-elevated); color: var(--ntw-fg); resize: vertical; }

.pp-actions { margin-block-start: 16px; }

#pp-fallback { border: 1px solid var(--ntw-danger); border-radius: 6px;
  background: #fef2f2; padding: 12px 14px; margin-block-start: 12px; }
#pp-fallback strong { color: var(--ntw-danger); }
#pp-fallback textarea { width: 100%; font-family: var(--pp-font-mono); font-size: 0.8em;
  margin-block-start: 8px; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) #pp-fallback { background: #3f1d1d; }
}
:root[data-theme="dark"] #pp-fallback { background: #3f1d1d; }
"""

_REVIEW_TEMPLATE = """<!doctype html>
<html lang="en"__THEME_ATTR__>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__CSS__
</style>
</head>
<body>
<main>
  <button id="pp-theme" type="button" aria-label="Toggle theme">&#9680;</button>
__HEADER_BLOCK__
  <div class="pp-content">
__CONTENT__
  </div>

  <label id="pp-overall-note-label" for="pp-overall-note">Overall note (optional)</label>
  <textarea id="pp-overall-note"
    placeholder="Anything for the whole call, beyond the per-line comments above"></textarea>

  <div class="pp-actions">
    <button type="button" id="pp-cancel">Cancel</button>
    <div class="pp-actions-end">
      <span id="pp-status"></span>
      <button type="button" id="pp-submit">Send review</button>
    </div>
  </div>

  <div id="pp-fallback" hidden>
    <strong id="pp-fallback-title">Couldn't reach Claude Code</strong>
    <p data-role="body" id="pp-fallback-reason"></p>
    <textarea readonly rows="6"></textarea>
  </div>
</main>

<script>
(function () {
  var SUBMIT_URL = __SUBMIT_URL__;
  var TIMEOUT_SECONDS = __TIMEOUT_SECONDS__;
  var STATUS_URL = __STATUS_URL__;
  var GENERATION = __GENERATION__;
  var THEME_KEY = 'no-text-wall:theme';
  // Same reasoning as ask's page — see there.
  var BASE_TITLE = document.title;
  document.title = '\\u2753 ' + BASE_TITLE;

  if (!document.documentElement.dataset.theme) {
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
    } catch (e) { /* private mode: fall back to the OS preference */ }
  }
  document.getElementById('pp-theme').addEventListener('click', function () {
    var dark = getComputedStyle(document.body).backgroundColor === 'rgb(30, 30, 30)';
    var next = document.documentElement.dataset.theme
      ? (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')
      : (dark ? 'light' : 'dark');
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* not persisted, still applied */ }
    send('theme', { theme: next });
  });

  // The whole row opens/closes its comment box — see page.py's _render_review_diff and
  // its CSS comment for why (three-variant review). Every page stays in the DOM the
  // whole time (deck-nav only toggles which .deck-slide is visible), so a comment
  // survives navigating away from its page and back without any extra state to track.
  // `frozen` (set by freeze() below, once Submit/Cancel has been sent) stops a row from
  // opening a box, and a box already open from accepting more text, after the comments
  // it would collect have already left — editing then would silently go nowhere.
  var frozen = false;
  var reviewRows = document.querySelectorAll('.pp-review-row-wrap');
  Array.prototype.slice.call(reviewRows).forEach(function (wrap) {
    var row = wrap.querySelector('.pp-review-row');
    var box = wrap.querySelector('.pp-review-comment-box');
    var textarea = box ? box.querySelector('textarea') : null;
    if (!row || !box || !textarea) return;
    function toggleBox() {
      if (frozen) return;
      var opening = !box.classList.contains('is-open');
      box.classList.toggle('is-open', opening);
      if (opening) textarea.focus();
    }
    row.addEventListener('click', toggleBox);
    row.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggleBox();
      }
    });
    textarea.addEventListener('input', function () {
      wrap.classList.toggle('has-comment', textarea.value.trim() !== '');
    });
  });

  function collectComments() {
    var comments = [];
    var boxes = document.querySelectorAll('.pp-review-comment-box textarea');
    Array.prototype.slice.call(boxes).forEach(function (textarea) {
      var text = textarea.value.trim();
      if (!text) return;
      comments.push({
        path: textarea.dataset.path,
        side: textarea.dataset.side,
        line: parseInt(textarea.dataset.line, 10),
        text: text
      });
    });
    return comments;
  }

  var submitBtn = document.getElementById('pp-submit');
  var cancelBtn = document.getElementById('pp-cancel');
  var status = document.getElementById('pp-status');
  var overallNote = document.getElementById('pp-overall-note');

  function collect() {
    return { comments: collectComments(), overall_note: overallNote.value.trim() };
  }

  function freeze(message) {
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    overallNote.readOnly = true;
    frozen = true;
    document.body.classList.add('pp-review-frozen');
    Array.prototype.slice.call(document.querySelectorAll('.pp-review-comment-box textarea'))
      .forEach(function (t) { t.readOnly = true; });
    status.textContent = message;
    stopHeartbeat();
  }

  var HEARTBEAT_MS = Math.min(60000, Math.max(2000, (TIMEOUT_SECONDS * 1000) / 4));
  var heartbeatTimer = null;
  function heartbeat() {
    if (document.visibilityState === 'visible') send('probe', {});
  }
  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(heartbeat, HEARTBEAT_MS);
  }
  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') heartbeat();
  });

  function fallback(payload, reason, title) {
    var box = document.getElementById('pp-fallback');
    var text = JSON.stringify(payload, null, 2);
    box.hidden = false;
    var textarea = box.querySelector('textarea');
    textarea.value = text;
    document.getElementById('pp-fallback-reason').textContent =
      reason + ' \\u2014 copy the text below and paste it to Claude Code.';
    document.getElementById('pp-fallback-title').textContent =
      title || "Couldn't reach Claude Code";
    textarea.select();
  }

  function startReusePolling() {
    if (!STATUS_URL) return;
    var timer = setInterval(async function () {
      try {
        var res = await fetch(STATUS_URL);
        if (!res.ok) return;
        var body = await res.json();
        if (body.generation !== GENERATION) {
          clearInterval(timer);
          location.reload();
        }
      } catch (e) { /* process gone — nothing left to reload for */ }
    }, 1500);
  }

  async function send(kind, data) {
    var payload = { kind: kind, data: data };
    try {
      var res = await fetch(SUBMIT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        if (kind !== 'probe') {
          fallback(payload, 'Claude Code rejected it (HTTP ' + res.status + ')');
        }
        return false;
      }
      return true;
    } catch (err) {
      if (kind !== 'probe') {
        fallback(payload, 'Claude Code is no longer listening (' + err.message + ')');
      }
      return false;
    }
  }

  function doneMessage(base) {
    if (STATUS_URL) return base + ' Waiting for the next thing\\u2026';
    return base + ' You can close this tab.';
  }

  submitBtn.addEventListener('click', async function () {
    var data = collect();
    freeze('Sending\\u2026');
    if (await send('submit', data)) {
      freeze(doneMessage('Review sent.'));
      document.title = '\\u2705 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  cancelBtn.addEventListener('click', async function () {
    // Cancel still sends whatever comments/overall note already exist — see
    // review.run_review's docstring and ADR 0002. Same payload shape as Submit; only
    // `kind` differs.
    var data = collect();
    freeze('Cancelling\\u2026');
    if (await send('cancel', data)) {
      freeze(doneMessage('Cancelled.'));
      document.title = '\\u2716 ' + BASE_TITLE;
      startReusePolling();
    } else status.textContent = 'Not sent.';
  });

  document.addEventListener('keydown', function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      submitBtn.click();
    }
  });

  send('probe', {});
  startHeartbeat();
}());
</script>
__DECK_SCRIPT__
__MERMAID__
</body>
</html>
"""


def _render_review_diff(path: str, diff: str) -> str:
    """One page's diff as `.pp-review-row` rows, each wrapped with its comment box.

    `parse_unified_diff` is what turns raw diff text into the (side, line) coordinate a
    comment on that row addresses — see `diff.DiffLine.side`/`.line`. A "hunk" row (the
    `@@ ...` header) is not addressable: there's no single line it could attach to.

    The whole row is the click/keyboard target for opening its comment box (`role`,
    `tabindex`; the actual open/close wiring is in `_REVIEW_TEMPLATE`'s script) — the
    bubble glyph is a hover affordance only, not a click target itself.
    """
    escaped_path = html.escape(path, quote=True)
    rows: list[DiffLine] = parse_unified_diff(diff)
    parts = [f'<div class="pp-review-diff" data-path="{escaped_path}">']
    for row in rows:
        text_html = html.escape(row.text) or "&nbsp;"
        if row.kind == "hunk":
            parts.append(f'<div class="pp-review-row hunk">{text_html}</div>')
            continue
        old = str(row.old_line) if row.old_line is not None else ""
        new = str(row.new_line) if row.new_line is not None else ""
        side = row.side or "new"
        line = row.line if row.line is not None else 0
        parts.append(
            f'<div class="pp-review-row-wrap" data-path="{escaped_path}" '
            f'data-side="{side}" data-line="{line}">'
            f'<div class="pp-review-row {row.kind}" role="button" tabindex="0" '
            'aria-label="Comment on this line">'
            f'<span class="pp-review-gutter">{old}</span>'
            f'<span class="pp-review-gutter">{new}</span>'
            f'<span class="pp-review-text">{text_html}</span>'
            '<span class="pp-review-comment-icon" aria-hidden="true">&#128172;</span>'
            "</div>"
            '<div class="pp-review-comment-box">'
            f'<textarea data-path="{escaped_path}" data-side="{side}" data-line="{line}" '
            'placeholder="Comment on this line"></textarea>'
            "</div>"
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _render_review_pages(pages: list[dict[str, object]], asset_base_url: str | None) -> str:
    """The deck of `.deck-slide`s for a `review` call — one per page, in order.

    Same free-navigation deck mechanics as `_render_deck` (`show`'s slides), reused here
    via base.css's `.deck-slide`/`.deck-nav` — but each slide is an addressable diff
    (`_render_review_diff`) rather than arbitrary Markdown, and the progress label reads
    "Page", not "Slide" — see `_review_deck_script`.
    """
    total = len(pages)
    # A single page has nothing to paginate between — the progress bar and Prev/Next
    # nav would just be inert chrome around the one diff, so both are skipped rather
    # than rendered and left doing nothing (caught by Operator on a one-page review).
    multi = total > 1
    initial_progress = round(100 / total) if total else 0
    progress_html = (
        (
            '<div class="pp-progress">'
            f'<span class="pp-progress-label" id="pp-deck-progress-label">Page 1 of {total}</span>'
            '<span class="pp-progress-track">'
            f'<span class="pp-progress-fill" id="pp-deck-progress-fill" '
            f'style="width: {initial_progress}%"></span>'
            "</span>"
            "</div>"
        )
        if multi
        else ""
    )
    slide_parts = []
    for index, page in enumerate(pages):
        active = " is-active" if index == 0 else ""
        path = str(page["path"])
        blurb = page.get("blurb")
        blurb_html = render_markdown(str(blurb), asset_base_url=asset_base_url) if blurb else ""
        diff_html = _render_review_diff(path, str(page["diff"]))
        slide_parts.append(
            f'<section class="deck-slide card{active}">'
            f'<div class="pp-review-path">{html.escape(path)}</div>'
            f"{blurb_html}"
            f"{diff_html}"
            "</section>"
        )
    nav_html = (
        (
            '<div class="deck-nav">'
            '<button type="button" data-deck-prev aria-label="Previous page">&larr;</button>'
            '<button type="button" data-deck-next aria-label="Next page">&rarr;</button>'
            "</div>"
        )
        if multi
        else ""
    )
    return progress_html + "\n".join(slide_parts) + nav_html


def _review_deck_script(needed: bool) -> str:
    """`_deck_script`'s nav mechanics, with a "Page N of M" label instead of "Slide".

    Loaded only when `_render_review_pages` rendered more than one page — with exactly
    one, there is nothing to paginate between, so the progress bar/Prev/Next markup this
    acts on doesn't exist either (see its `multi` check).
    """
    if not needed:
        return ""
    return """<script>
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck-slide'));
  var progressLabel = document.getElementById('pp-deck-progress-label');
  var progressFill = document.getElementById('pp-deck-progress-fill');
  var index = 0;
  function show(next) {
    slides[index].classList.remove('is-active');
    index = (next + slides.length) % slides.length;
    slides[index].classList.add('is-active');
    if (progressLabel) progressLabel.textContent = 'Page ' + (index + 1) + ' of ' + slides.length;
    if (progressFill) {
      progressFill.style.width = Math.round((index + 1) / slides.length * 100) + '%';
    }
  }
  var prevBtn = document.querySelector('[data-deck-prev]');
  var nextBtn = document.querySelector('[data-deck-next]');
  if (prevBtn) prevBtn.addEventListener('click', function () { show(index - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function () { show(index + 1); });
  document.addEventListener('keydown', function (event) {
    if (event.target.matches('input, textarea, [contenteditable]')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'ArrowRight') show(index + 1);
    if (event.key === 'ArrowLeft') show(index - 1);
  });
}());
</script>"""


def build_review_page(
    *,
    pages: list[dict[str, object]],
    submit_url: str,
    timeout_seconds: float,
    header: str | None = None,
    title: str = "Claude Code",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    """Return the complete HTML document for one `review` call.

    `pages` is one dict per `review.Page` (`path`, `diff` — raw unified diff text —,
    optional `blurb` Markdown), rendered as a deck of freely-navigable pages, same nav
    mechanics as `show`'s slides (`_render_deck`) but each page is an addressable diff:
    every line gets a gutter comment toggle, resolved server-side (`diff.
    parse_unified_diff`) to the old/new-file line number a comment on it addresses — see
    CONTEXT.md's "Review" section. `header` is one Markdown block shown above every page.

    Submit always succeeds — there is nothing to validate, since an empty review (no
    comments, no overall note) is itself a valid answer. Cancel sends the same payload
    shape as Submit, comments and overall note included, rather than discarding them —
    see `review.run_review`'s docstring and ADR 0002.

    `submit_url`/`timeout_seconds`/`status_url`/`generation` mirror `build_page`'s — see
    there.
    """
    css = _load_base_css() + _ACTIONS_CSS + _REVIEW_ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    asset_base_url = _asset_base_url(status_url)
    header_html = (
        f'<div class="pp-content">{render_markdown(header, asset_base_url=asset_base_url)}</div>'
        if header
        else ""
    )
    content_html = _render_review_pages(pages, asset_base_url)

    return (
        _REVIEW_TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__HEADER_BLOCK__", header_html)
        .replace("__CONTENT__", content_html)
        .replace("__DECK_SCRIPT__", _review_deck_script(len(pages) > 1))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(content_html, header_html)))
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
    )


def _load_base_css() -> str:
    return resources.files(__package__).joinpath("assets/base.css").read_text(encoding="utf-8")


def parse_accent(value: str) -> tuple[int, int, int]:
    """Parse a `#rrggbb` (or `rrggbb`) hex colour into its RGB components.

    Raises `InvalidAccent` naming the value, so a bad `PRETTY_PROMPT_ACCENT` fails with
    a message pointing at the setting rather than a bare `ValueError` from inside a
    string slice.
    """
    stripped = value.strip().removeprefix("#")
    if len(stripped) != 6 or not all(c in "0123456789abcdefABCDEF" for c in stripped):
        raise InvalidAccent(f"{value!r} is not a #rrggbb hex colour")
    return (int(stripped[0:2], 16), int(stripped[2:4], 16), int(stripped[4:6], 16))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance, used only to pick a black-or-white contrast colour."""

    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_hex(rgb: tuple[int, int, int]) -> str:
    return "#0a0a0a" if _relative_luminance(rgb) > 0.5 else "#ffffff"


def _blend_hex(rgb: tuple[int, int, int], onto: tuple[int, int, int], alpha: float) -> str:
    mixed = (round(fg * alpha + bg * (1 - alpha)) for fg, bg in zip(rgb, onto, strict=True))
    return "#" + "".join(f"{c:02x}" for c in mixed)


def _accent_css(accent_hex: str) -> str:
    """CSS overriding `--ntw-accent` (and what's derived from it) for a custom accent.

    Same three selectors `base.css` and this page's own palette override use — see the
    module docstring on why nothing here touches `base.css` itself — placed after both
    in the stylesheet so equal specificity resolves to source order and this wins.
    """
    rgb = parse_accent(accent_hex)
    hex_value = "#" + "".join(f"{c:02x}" for c in rgb)
    contrast = _contrast_hex(rgb)
    light_tint = _blend_hex(rgb, _LIGHT_BG_RGB, _LIGHT_TINT_ALPHA)
    dark_tint = _blend_hex(rgb, _DARK_BG_RGB, _DARK_TINT_ALPHA)
    return f"""
:root {{
  --ntw-accent: {hex_value};
  --ntw-accent-contrast: {contrast};
  --ntw-verdict-bg: {light_tint};
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ntw-accent: {hex_value};
    --ntw-accent-contrast: {contrast};
    --ntw-verdict-bg: {dark_tint};
  }}
}}
:root[data-theme="dark"] {{
  --ntw-accent: {hex_value};
  --ntw-accent-contrast: {contrast};
  --ntw-verdict-bg: {dark_tint};
}}
"""


def _js_string(value: object) -> str:
    """JSON for embedding inside a <script> block.

    `<` is escaped so that a value containing `</script>` cannot close the element early.
    """
    return json.dumps(value).replace("<", "\\u003c")


# A body past this length, or one carrying an image or a fenced code block, is judged
# "long" — it collapses behind a drawer. A short one is shown inline instead, so a
# one-line body never costs the reader an extra click just to see it. Cheap and a
# little arbitrary, but consistent, unlike guessing per-call which bodies "feel" long.
_LONG_BODY_CHARS = 400


def _is_long_body(body_html: str) -> bool:
    return "<img" in body_html or "<pre" in body_html or len(body_html) > _LONG_BODY_CHARS


def _has_image_body(option: dict[str, object]) -> bool:
    body = option.get("body")
    return isinstance(body, str) and "![" in body


def _asset_base_url(status_url: str | None) -> str | None:
    """The `/asset?t=...` URL local `file://` images should rewrite to, or None.

    Derived from `status_url` rather than taking its own parameter: the two are set
    together exactly when this page is served by a `listener.SessionListener` (see
    `build_page`'s docstring) — the same, and only, case where a `file://` image needs
    rewriting at all, since a plain `file://`-opened page loads one directly. Same host,
    port and token as `status_url`, just a different path.
    """
    if status_url is None:
        return None
    split = urlsplit(status_url)
    return f"{split.scheme}://{split.netloc}/asset?{split.query}"


def _is_gallery(options: list[dict[str, object]]) -> bool:
    """Two or more image-bearing options make this a gallery, auto-detected the same way
    a long body already auto-collapses into a drawer — nothing for the caller to flag.

    One image alone stays the ordinary layout: comparing needs at least two things next
    to each other, and a single-image option is already well served by the existing
    inline-or-drawer body.
    """
    return sum(1 for option in options if _has_image_body(option)) >= 2


_CHEVRON_SVG = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)


def _render_options(
    options: list[dict[str, object]],
    multi_select: bool,
    *,
    name: str = "pp-option",
    data_step: str | None = None,
    gallery: bool = False,
    asset_base_url: str | None = None,
) -> str:
    if not options:
        return ""
    kind = "checkbox" if multi_select else "radio"
    step_attr = f' data-step="{data_step}"' if data_step is not None else ""
    rows: list[str] = []
    for option in options:
        option_id = html.escape(str(option["id"]), quote=True)
        raw_label = str(option["label"])
        label = html.escape(raw_label, quote=True)
        # Inline rendering only — same reasoning as the caveat below: block syntax (a
        # leading `>`, a heading) has nowhere sensible to go in a one-line title, so it
        # stays literal text instead of breaking the row. Bold/code/links/etc. do render,
        # where previously they silently didn't, unlike everywhere else in the document.
        label_html = render_inline(raw_label, asset_base_url=asset_base_url)
        body = option.get("body")
        # aria-label, not reliance on the wrapping element, because the long-body case
        # below wraps this input in <summary> rather than <label> — <summary> confers no
        # implicit accessible name the way <label> does, so the input would otherwise
        # announce with none at all. Plain-escaped, not label_html: an aria-label is read
        # as text, not markup, so it must stay free of HTML tags.
        checked_attr = " checked" if option.get("selected") else ""
        select_html = (
            f'<input type="{kind}" name="{name}" data-option-id="{option_id}"{step_attr}'
            f'{checked_attr} aria-label="{label}">'
            f'<span class="pp-option-label">{label_html}</span>'
            + (
                '<span class="pp-option-recommended">Recommended</span>'
                if option.get("recommended")
                else ""
            )
        )
        # Inside the select row (the <label> or the drawer's <summary>) rather than a
        # sibling of the body: a closed <details> shows only its <summary>, and the
        # caveat has to stay visible without opening anything. Inline rendering only, so
        # block syntax in a caveat stays literal text instead of breaking the row.
        caveat = option.get("caveat")
        caveat_inline = render_inline(str(caveat), asset_base_url=asset_base_url) if caveat else ""
        caveat_html = f'<div class="pp-option-caveat">{caveat_inline}</div>' if caveat else ""

        if not body:
            rows.append(
                f'    <li class="pp-option"><label class="pp-option-select">'
                f"{select_html}{caveat_html}</label></li>"
            )
            continue

        body_html = render_markdown(str(body), asset_base_url=asset_base_url)
        body_div = f'<div class="pp-option-body">{body_html}</div>'

        # A gallery never drawers its images behind a click — the whole point is seeing
        # them at once — so it always takes the inline branch, long body or not.
        if gallery or not _is_long_body(body_html):
            rows.append(
                f'    <li class="pp-option"><label class="pp-option-select">'
                f"{select_html}{caveat_html}</label>{body_div}</li>"
            )
            continue

        # The row is the drawer's <summary>, but only the Details pill opens it; the
        # page's script turns any other click on the row into selecting the option.
        open_attr = " open" if option.get("recommended") else ""
        details = f'<span class="pp-option-details" aria-hidden="true">Details{_CHEVRON_SVG}</span>'
        rows.append(
            f'    <li class="pp-option"><details class="pp-option-drawer" '
            f'data-option-id="{option_id}"{open_attr}>'
            f'<summary class="pp-option-select">{select_html}{details}{caveat_html}</summary>'
            f"{body_div}</details></li>"
        )
    ul_class = "pp-options pp-options-gallery" if gallery else "pp-options"
    return f'  <ul class="{ul_class}">\n' + "\n".join(rows) + "\n  </ul>"


def _theme_attr(theme: str | None) -> str:
    """The ` data-theme="..."` to splice into `<html lang="en"...>`, or "" if unset.

    Baked into the markup rather than left to the page's own localStorage-reading script
    so the saved theme applies on the very first paint — no flash of the other theme
    while the script catches up.
    """
    if theme not in _THEME_STATES:
        return ""
    return f' data-theme="{theme}"'


# Pinned to the exact version jsDelivr resolved `mermaid@11` to when this was written
# (2026-09-21), not left floating: a floating tag means the page's behaviour can change
# without this repo changing, which is the one thing vendoring (the no-text-wall default)
# avoids and a CDN load reintroduces. See page.py's module docstring for why a CDN was
# chosen here anyway over vendoring the ~3.5MB bundle.
_MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"


def _needs_mermaid(*fragments: str) -> bool:
    """True if any rendered fragment carries a ```mermaid fence (see markdown.py)."""
    return any('class="mermaid"' in fragment for fragment in fragments)


def _needs_wide_layout(*fragments: str) -> bool:
    """True if any rendered fragment carries a ```diff/code fence or a table.

    These are exactly the content shapes that hit their own horizontal scrollbar
    (`.pp-diff`/`pre.code-block`/`table` all set `overflow-x: auto` or scroll their
    container) well before an ordinary question or report would — `review`'s page hit
    this with a fixed max-width that left most of the window unused (see its own
    width fix). Widened the same way a gallery question already is (`_is_gallery`),
    not by a length threshold: a merely long paragraph doesn't benefit from extra
    width the way a wide structured block does — prose gets harder to read wider, not
    easier.
    """
    return any(
        'class="pp-diff"' in fragment or 'class="code-block"' in fragment or "<table" in fragment
        for fragment in fragments
    )


def _mermaid_script(needed: bool) -> str:
    """The `<script>` pair loading and running mermaid, or "" if this page has none.

    Loaded only on a page that actually has a ```mermaid fence — every other page keeps
    the "no network fetch but the one POST" contract intact. `theme` is read once, from
    the page's own resolved theme (data-theme if set, else the OS preference) — it does
    not react to the in-page toggle afterwards; mermaid has no live re-render for that,
    same gap no-text-wall's DIAGRAMS.md already discloses for its own vendored version.
    """
    if not needed:
        return ""
    return f"""<script src="{_MERMAID_SRC}"></script>
<script>
(function () {{
  var dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === 'dark'
    : matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.initialize({{
    startOnLoad: false,
    theme: dark ? 'dark' : 'default',
    securityLevel: 'strict',
  }});
  document.querySelectorAll('.mermaid').forEach(function (el) {{
    mermaid.run({{ nodes: [el] }});
  }});
}}());
</script>"""


def _deck_script(needed: bool) -> str:
    """The `<script>` driving `.deck-slide` prev/next/keyboard nav, or "" if not a deck.

    Loaded only when `build_show_page` was given `slides` — a plain-content page has no
    `.deck-slide` elements for this to act on. The nav mechanics (prev/next, keyboard,
    wraparound) are the same ones exercised in no-text-wall's `TEMPLATES.md` `deck`
    template; the progress bar it drives instead of that template's plain "N / total"
    span reuses `ask`'s own wizard-progress widget (`.pp-progress`) for visual
    consistency across pretty-prompt's two multi-step surfaces.
    """
    if not needed:
        return ""
    return """<script>
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck-slide'));
  var progressLabel = document.getElementById('pp-deck-progress-label');
  var progressFill = document.getElementById('pp-deck-progress-fill');
  var index = 0;
  function show(next) {
    slides[index].classList.remove('is-active');
    index = (next + slides.length) % slides.length;
    slides[index].classList.add('is-active');
    if (progressLabel) progressLabel.textContent = 'Slide ' + (index + 1) + ' of ' + slides.length;
    if (progressFill) {
      progressFill.style.width = Math.round((index + 1) / slides.length * 100) + '%';
    }
  }
  var prevBtn = document.querySelector('[data-deck-prev]');
  var nextBtn = document.querySelector('[data-deck-next]');
  if (prevBtn) prevBtn.addEventListener('click', function () { show(index - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function () { show(index + 1); });
  document.addEventListener('keydown', function (event) {
    if (event.target.matches('input, textarea, [contenteditable]')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'ArrowRight') show(index + 1);
    if (event.key === 'ArrowLeft') show(index - 1);
  });
}());
</script>"""


def build_page(
    *,
    question: str,
    options: list[dict[str, object]],
    multi_select: bool,
    allow_free_text: bool,
    free_text_prefill: str = "",
    submit_url: str,
    timeout_seconds: float,
    allow_defer: bool = False,
    title: str = "Claude Code is asking",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    """Return the complete HTML document for one `ask` call.

    `timeout_seconds` must match the value the listener was actually given: it only
    paces the page's own idle-avoiding heartbeat (see the inline script), which sends
    faster for a short timeout so it still lands comfortably inside the window.

    `accent` is a `#rrggbb` hex colour overriding the built-in green, or None to use it
    as-is. Raises `InvalidAccent` if set and unparseable — callers reading it from
    `PRETTY_PROMPT_ACCENT` are expected to validate once at startup (see
    `server.accent_config_problem`) so this only re-raises the same problem, never
    surfaces a new one mid-call.

    `theme` is `"light"`, `"dark"`, or None to follow the OS preference until the page's
    own toggle is used — see `read_saved_theme`.

    `status_url` and `generation` are only set when this page is served by a
    `listener.SessionListener` for tab reuse: once answered, the page polls `status_url`
    and reloads the moment the served generation moves past `generation`, landing on the
    next question without a new tab. Leave both at their defaults for the ordinary
    one-shot, `file://`-opened page, where there is nothing to poll.
    """
    css = _load_base_css() + _ACTIONS_CSS + _ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    free_text = ""
    if allow_free_text:
        free_text = (
            '  <label class="pp-free-text">Anything else'
            '<textarea id="pp-free-text" rows="4" '
            'placeholder="Optional — type a free-text answer">\n'
            f"{html.escape(free_text_prefill)}</textarea></label>"
        )

    if options and allow_free_text:
        hint = "Pick an option, write an answer, or both."
    elif options:
        hint = "Pick an option." if not multi_select else "Pick one or more options."
    else:
        hint = "Write an answer."

    gallery = _is_gallery(options)
    asset_base_url = _asset_base_url(status_url)
    question_html, question_class = _question_html(question, asset_base_url)
    options_html = _render_options(
        options, multi_select, gallery=gallery, asset_base_url=asset_base_url
    )
    wide = gallery or _needs_wide_layout(question_html, options_html)

    return (
        _TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__MAIN_EXTRA__", ' class="pp-wide"' if wide else "")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace(
            "__QUESTION_BLOCK__",
            _with_defer(
                f'  <section class="pp-question{question_class}">\n{question_html}\n  </section>',
                allow_defer,
            ),
        )
        .replace("__OPTIONS__", options_html)
        .replace("__FREE_TEXT__", free_text)
        .replace("__HINT__", html.escape(hint, quote=True))
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(question_html, options_html)))
    )


# The "Not yet" pill sits beside the question rather than in the action row, and a
# banner above the options says the question is deferred while it is: picked over an
# action-row button (easy to overlook) and a checkbox row under the options in a
# three-way visual A/B/C. The page's script (wireDefer) does the locking.
_DEFER_CONTROL = '<button type="button" class="pp-defer" aria-pressed="false">Not yet</button>'
_DEFER_BANNER = (
    '<div class="pp-defer-banner" hidden>Deferred: this stays open for a later round.</div>'
)


def _question_html(question: str, asset_base_url: str | None) -> tuple[str, str]:
    """The question's HTML and the class marking a long one, so only its lead stays bold."""
    if len(first_paragraph(question)) <= LONG_QUESTION_CHARS:
        return render_markdown(question, asset_base_url=asset_base_url), ""
    leading = bold_lead(question) or question
    return render_markdown(leading, asset_base_url=asset_base_url), " pp-long"


def _with_defer(question_block: str, allow_defer: bool) -> str:
    """`question_block` with the defer pill beside it and its banner after, if allowed."""
    if not allow_defer:
        return question_block
    return f'<div class="pp-question-row">{question_block}{_DEFER_CONTROL}</div>\n{_DEFER_BANNER}'


def _render_step(index: int, spec: dict[str, object], asset_base_url: str | None = None) -> str:
    question_html, question_class = _question_html(str(spec.get("question", "")), asset_base_url)
    raw_options = spec.get("options")
    options = list(raw_options) if isinstance(raw_options, list) else []
    multi_select = bool(spec.get("multi_select"))
    options_html = _render_options(
        options,
        multi_select,
        name=f"pp-option-step-{index}",
        data_step=str(index),
        gallery=_is_gallery(options),
        asset_base_url=asset_base_url,
    )
    free_text_html = ""
    if spec.get("allow_free_text", True):
        free_text_html = (
            f'    <label class="pp-free-text">Anything else'
            f'<textarea id="pp-free-text-{index}" data-step="{index}" rows="3" '
            'placeholder="Optional — type a free-text answer">\n'
            f"{html.escape(str(spec.get('free_text') or ''))}</textarea></label>"
        )
    # The wizard's JS toggles `pp-step-active` on navigation; the first step starts with
    # it already set so the page shows question one even before the script has run.
    active = " pp-step-active" if index == 0 else ""
    question_block = _with_defer(
        f'    <div class="pp-question{question_class}">{question_html}</div>',
        bool(spec.get("allow_defer")),
    )
    return (
        f'  <section class="pp-step{active}" data-step-index="{index}">\n'
        f"{question_block}\n"
        f"{options_html}\n"
        f"{free_text_html}\n"
        "  </section>"
    )


def build_step_fragments(
    questions: list[dict[str, object]], asset_base_url: str | None = None
) -> list[str]:
    """One `<section class="pp-step">` per question, what `build_multi_page` puts in the page.

    `asset_base_url` is the value `build_multi_page` derived from its own `status_url`.
    """
    return [_render_step(i, spec, asset_base_url) for i, spec in enumerate(questions)]


def _render_header(header: str, asset_base_url: str | None = None) -> str:
    """The multi-question page's header block, or "" if there is none.

    Muted text with no box, above the progress bar: picked over a clipped card and an
    accent-rule panel in a three-way visual A/B/C, as the lightest option that keeps the
    question the focus of every step. A header judged long by the same rule as an
    option body collapses behind a "Context" toggle; when it is a single flat list, the
    toggle also says how many items it holds, which is what a list of settled decisions
    usually is.
    """
    if not header.strip():
        return ""
    header_html = render_markdown(header, asset_base_url=asset_base_url)
    if not _is_long_body(header_html):
        return f'  <div class="pp-header">{header_html}</div>'
    lists = header_html.count("<ul>") + header_html.count("<ol>")
    count = ""
    if lists == 1:
        count = f' <span class="pp-header-count">{header_html.count("<li>")} items</span>'
    return (
        f'  <details class="pp-header"><summary>Context{count}</summary>'
        f'<div class="pp-header-body">{header_html}</div></details>'
    )


def build_multi_page(
    *,
    questions: list[dict[str, object]],
    submit_url: str,
    header: str = "",
    timeout_seconds: float,
    title: str = "Claude Code is asking",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
) -> str:
    """Return the complete HTML document for an `ask` call carrying several questions.

    Prototype: a sibling of `build_page` rather than a generalisation of it, so the
    well-exercised single-question page and its wire format (`{selected, free_text,
    expanded}`) stay exactly as they were. Each entry of `questions` takes the same keys
    `build_page` takes for one question (`question`, `options`, `multi_select`,
    `allow_free_text`). The page's wire format here is `{"answers": [{selected,
    free_text, expanded}, ...]}`, one entry per question, in the order given — see
    `ask.run_ask` for how it's unpacked.

    Presented as a wizard, one question per screen with a progress bar and Back/Next —
    picked over showing every question stacked at once and over an accordion in a
    three-way visual A/B/C (see the "paging style" pick in project history).

    `header` is Markdown shown above the progress bar, so on every step; see
    `_render_header`.

    `theme`, `status_url` and `generation` are as `build_page` takes them.
    """
    css = _load_base_css() + _ACTIONS_CSS + _ADDENDUM + _MULTI_ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    total = len(questions)
    asset_base_url = _asset_base_url(status_url)
    steps_html = "\n".join(build_step_fragments(questions, asset_base_url=asset_base_url))
    header_html = _render_header(header, asset_base_url=asset_base_url)
    initial_progress = round(100 / total) if total else 0
    initial_label = f"Question 1 of {total}"
    initial_next = "Next" if total > 1 else "Send answers"
    any_gallery = any(
        _is_gallery(options) if isinstance(options := spec.get("options"), list) else False
        for spec in questions
    )
    wide = any_gallery or _needs_wide_layout(steps_html, header_html)

    return (
        _MULTI_TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__MAIN_EXTRA__", ' class="pp-wide"' if wide else "")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__HEADER__", header_html)
        .replace("__STEPS__", steps_html)
        .replace("__INITIAL_LABEL__", html.escape(initial_label, quote=True))
        .replace("__INITIAL_PROGRESS__", str(initial_progress))
        .replace("__INITIAL_NEXT_LABEL__", initial_next)
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STEP_COUNT__", str(total))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__MERMAID_SRC__", _js_string(_MERMAID_SRC))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(header_html, steps_html)))
    )

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

from .markdown import render_markdown

__all__ = [
    "ACCENT_ENV_VAR",
    "InvalidAccent",
    "build_multi_page",
    "build_page",
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
.pp-question > :last-child { margin-block-end: 0; }
.pp-question > :not(:first-child) { color: var(--ntw-fg-muted); font-size: 0.9em; }
.pp-question img { max-width: 100%; height: auto; border-radius: 6px; }

.pp-options { display: flex; flex-direction: column; gap: 6px; margin: 0 0 16px;
  padding: 0; list-style: none; }
.pp-option { border: 1px solid var(--ntw-border); border-radius: 6px;
  background: var(--ntw-bg-elevated); padding: 8px 10px; }
.pp-option:hover { border-color: var(--ntw-accent); }
.pp-option:has(input:checked) { border-color: var(--ntw-accent);
  background: var(--ntw-verdict-bg); }
/* An option with a body is a <details class="pp-option-drawer"> whose <summary> is the
   whole row — clicking the label or the chevron opens it, clicking the checkbox/radio
   (which stops its own click from bubbling, see the script below) selects it instead.
   Nesting the disclosure inside the row this way, rather than as a sibling with its own
   "Details" control, was the point: one row, one obvious click target for each action,
   no second label to word. An option with no body is just the <label> below, un-nested. */
.pp-option-drawer > summary { list-style: none; cursor: pointer; }
.pp-option-drawer > summary::-webkit-details-marker { display: none; }
.pp-option-select { display: flex; gap: 8px; align-items: center; cursor: pointer; margin: 0; }
.pp-option-select input { margin: 0; accent-color: var(--ntw-accent); }
.pp-option-label { font-family: var(--pp-font-mono); font-weight: 500; font-size: 0.9em; }
.pp-option-chevron { margin-inline-start: auto; flex: none; color: var(--ntw-fg-muted);
  transition: transform .15s; }
.pp-option-chevron::before { content: "\\203A"; }
.pp-option-drawer[open] .pp-option-chevron { transform: rotate(90deg); }
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

.pp-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pp-actions button { border-radius: 6px; padding: 7px 14px; font: inherit;
  font-family: var(--pp-font-mono); font-size: 0.85em; font-weight: 500; cursor: pointer; }
/* #pp-next is the wizard page's equivalent of #pp-submit (the primary action), and
   #pp-back its equivalent of #pp-cancel (secondary) — grouped here rather than
   duplicated in _MULTI_ADDENDUM, since neither pair of ids ever coexists on one page. */
#pp-submit, #pp-next { border: 1px solid var(--ntw-accent); background: var(--ntw-accent);
  color: var(--ntw-accent-contrast); }
#pp-cancel, #pp-back { border: 1px solid var(--ntw-border); background: transparent; color: var(--ntw-fg); }
.pp-actions button:disabled { opacity: 0.5; cursor: default; }
#pp-status { color: var(--ntw-fg-muted); font-size: 0.85em; }

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
.pp-progress { display: flex; align-items: center; gap: 10px; margin: 0 40px 18px 0; }
.pp-progress-label { font-size: 0.8em; color: var(--ntw-fg-muted); white-space: nowrap; }
.pp-progress-track { flex: 1; height: 4px; border-radius: 999px; background: var(--ntw-border);
  overflow: hidden; }
.pp-progress-fill { height: 100%; background: var(--ntw-accent); border-radius: 999px;
  transition: width .15s; }
.pp-step { display: none; }
.pp-step.pp-step-active { display: block; }
.pp-step .pp-question { margin: 0 0 10px; }
.pp-step .pp-free-text { margin: 10px 0 0; }
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
  <section class="pp-question">
__QUESTION__
  </section>

  <form id="pp-form" novalidate>
__OPTIONS__
__FREE_TEXT__
    <div class="pp-actions">
      <button type="submit" id="pp-submit" disabled>Send answer</button>
      <button type="button" id="pp-cancel">Cancel</button>
      <span id="pp-status">__HINT__</span>
    </div>
  </form>

  <div id="pp-fallback" hidden>
    <strong>Couldn't reach Claude Code</strong>
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
  // So the tab bar shows whether this question is done without switching to the tab —
  // useful once several are open, or once one is answered and left to wait its turn.
  var BASE_TITLE = document.title;
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
  var drawers = Array.prototype.slice.call(document.querySelectorAll('.pp-option-drawer[data-option-id]'));
  // `expanded` (below, in collect()) is "was this ever opened", not "is it open right
  // now" — with several long-bodied options, closing one after reading it to get to the
  // next is the expected motion, and a snapshot at submit time would read that as never
  // having been opened at all. The initial `open` attribute (recommended: true) counts
  // as having been shown too, seeded here rather than waiting for a first `toggle`.
  drawers.forEach(function (d) {
    d._wasExpanded = d.open;
    d.addEventListener('toggle', function () { if (d.open) d._wasExpanded = true; });
  });

  // A reload on a reused tab (see STATUS_URL/startReusePolling below) always means a
  // genuinely new question, never the one just answered — but the browser doesn't know
  // that, and some (Firefox in particular) restore form field values across a plain
  // reload keyed by field identity/position. Since the new question's fields can occupy
  // the same identity as the old ones, that restore has to be undone before anything
  // else reads these fields, or the new question can silently start pre-answered.
  inputs.forEach(function (i) { i.checked = false; });
  if (freeText) freeText.value = '';

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

  function collect() {
    return {
      selected: inputs.filter(function (i) { return i.checked; })
                      .map(function (i) { return i.dataset.optionId; }),
      free_text: freeText ? freeText.value.trim() : '',
      // Every drawer opened at any point this page has been up, not just the ones still
      // open at submit time — see the `_wasExpanded` tracking above.
      expanded: drawers.filter(function (d) { return d._wasExpanded; })
                        .map(function (d) { return d.dataset.optionId; })
    };
  }

  // Free text is additive, never exclusive: either a checked option or a non-empty box
  // is enough on its own to make the answer sendable.
  //
  // The hint only shows while the form isn't answerable yet: once something is picked
  // or typed, "Pick an option, write an answer, or both." has done its job and stays
  // out of the way rather than sitting there as clutter for the rest of the call.
  function refresh() {
    var d = collect();
    submitBtn.disabled = d.selected.length === 0 && d.free_text === '';
    status.hidden = !submitBtn.disabled;
  }

  inputs.forEach(function (i) { i.addEventListener('change', refresh); });
  // An option with a long body is a <details> whose <summary> is the whole row (see
  // page.py's _render_options), so a click on the checkbox/radio would otherwise also
  // reach <summary> and toggle the drawer open. Stopping it here is what keeps
  // selecting and opening independent.
  inputs.forEach(function (i) {
    i.addEventListener('click', function (e) { e.stopPropagation(); });
  });
  if (freeText) freeText.addEventListener('input', refresh);

  function freeze(message) {
    inputs.forEach(function (i) { i.disabled = true; });
    if (freeText) freeText.readOnly = true;
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
    // Catches up immediately on refocus, rather than waiting up to HEARTBEAT_MS for the
    // next tick, in case the deadline is close by the time the tab is looked at again.
    if (document.visibilityState === 'visible') heartbeat();
  });

  function fallback(payload, reason) {
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
        if (kind !== 'probe') {
          fallback(payload, 'Claude Code rejected the answer (HTTP ' + res.status + ')');
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

  // "You can close this tab" is only true on the ordinary one-shot page — a reused tab
  // (STATUS_URL set) is about to reload itself for the next question instead.
  function doneMessage(base) {
    return STATUS_URL
      ? base + ' Waiting for the next question\\u2026'
      : base + ' You can close this tab.';
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
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
      <button type="button" id="pp-back" disabled>Back</button>
      <button type="button" id="pp-next" disabled>__INITIAL_NEXT_LABEL__</button>
      <button type="button" id="pp-cancel">Cancel</button>
      <span id="pp-status"></span>
    </div>
  </form>

  <div id="pp-fallback" hidden>
    <strong>Couldn't reach Claude Code</strong>
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
  // So the tab bar shows whether this question is done without switching to the tab —
  // useful once several are open, or once one is answered and left to wait its turn.
  var BASE_TITLE = document.title;
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

  var steps = Array.prototype.slice.call(document.querySelectorAll('.pp-step'));
  var backBtn = document.getElementById('pp-back');
  var nextBtn = document.getElementById('pp-next');
  var cancelBtn = document.getElementById('pp-cancel');
  var status = document.getElementById('pp-status');
  var progressLabel = document.getElementById('pp-progress-label');
  var progressFill = document.getElementById('pp-progress-fill');
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input[data-option-id]'));
  var drawers = Array.prototype.slice.call(document.querySelectorAll('.pp-option-drawer[data-option-id]'));
  var freeTexts = Array.prototype.slice.call(document.querySelectorAll('textarea[data-step]'));
  var current = 0;

  // A reload on a reused tab (see STATUS_URL/startReusePolling below) always means a
  // genuinely new set of questions, never the ones just answered — but the browser
  // doesn't know that, and some (Firefox in particular) restore form field values across
  // a plain reload keyed by field identity/position. Since the new questions' fields can
  // occupy the same identity as the old ones, that restore has to be undone before
  // anything else reads these fields, or a new question can silently start pre-answered.
  inputs.forEach(function (i) { i.checked = false; });
  freeTexts.forEach(function (t) { t.value = ''; });

  // `expanded` (below, in collect()) is "was this ever opened", not "is it open right
  // now" — with several long-bodied options, closing one after reading it to move to
  // the next is the expected motion, and a snapshot at submit time would read that as
  // never having been opened. The initial `open` attribute (recommended: true) counts
  // as having been shown too, seeded here rather than waiting for a first `toggle`.
  drawers.forEach(function (d) {
    d._wasExpanded = d.open;
    d.addEventListener('toggle', function () { if (d.open) d._wasExpanded = true; });
  });

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
  function stepAnswered(step) {
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
                                 .map(function (d) { return d.dataset.optionId; })
      });
    }
    return { answers: answers };
  }

  function showStep(index) {
    steps.forEach(function (el, i) { el.classList.toggle('pp-step-active', i === index); });
    progressLabel.textContent = 'Question ' + (index + 1) + ' of ' + STEP_COUNT;
    progressFill.style.width = ((index + 1) / STEP_COUNT * 100) + '%';
    backBtn.disabled = index === 0;
    nextBtn.textContent = index === STEP_COUNT - 1 ? 'Send answers' : 'Next';
    refresh();
  }

  // Only the current step's answer gates Next — earlier steps were already checked on
  // their own way past, and later ones haven't been seen yet.
  function refresh() {
    nextBtn.disabled = !stepAnswered(current);
  }

  inputs.forEach(function (i) { i.addEventListener('change', refresh); });
  // See page.py's _render_options: a long body wraps the input in <summary>, so a click
  // on the checkbox/radio would otherwise also toggle the drawer open.
  inputs.forEach(function (i) {
    i.addEventListener('click', function (e) { e.stopPropagation(); });
  });
  freeTexts.forEach(function (t) { t.addEventListener('input', refresh); });

  function freeze(message) {
    inputs.forEach(function (i) { i.disabled = true; });
    freeTexts.forEach(function (t) { t.readOnly = true; });
    backBtn.disabled = true;
    nextBtn.disabled = true;
    cancelBtn.disabled = true;
    status.hidden = false;
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

  function fallback(payload, reason) {
    var box = document.getElementById('pp-fallback');
    var text = JSON.stringify(payload, null, 2);
    box.hidden = false;
    var textarea = box.querySelector('textarea');
    textarea.value = text;
    document.getElementById('pp-fallback-reason').textContent =
      reason + ' \\u2014 copy the text below and paste it to Claude Code.';
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
        if (kind !== 'probe') {
          fallback(payload, 'Claude Code rejected the answer (HTTP ' + res.status + ')');
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
      nextBtn.click();
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

/* Same visual language as _ADDENDUM's .pp-actions/button, duplicated rather than
   shared for the same reason _SHOW_ADDENDUM as a whole is its own block — see the
   module docstring. */
.pp-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-block-start: 22px; position: relative; }
.pp-actions button { border-radius: 6px; padding: 7px 14px; font: inherit;
  font-family: var(--pp-font-mono); font-size: 0.85em; font-weight: 500; cursor: pointer;
  border: 1px solid var(--ntw-border); background: transparent; color: var(--ntw-fg); }
#pp-ack { border-color: var(--ntw-accent); background: var(--ntw-accent);
  color: var(--ntw-accent-contrast); }
.pp-actions button:disabled { opacity: 0.5; cursor: default; }
#pp-status { color: var(--ntw-fg-muted); font-size: 0.85em; }

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
<main>
  <button id="pp-theme" type="button" aria-label="Toggle theme">&#9680;</button>
  <div class="pp-content">
__CONTENT__
  </div>

  <div class="pp-actions">
__SAVE_BUTTON__
__ACK_BUTTON__
    <span id="pp-status"></span>
  </div>

  <div id="pp-fallback" hidden>
    <strong>Couldn't reach Claude Code</strong>
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
        if (kind !== 'probe') fallback(payload, 'Claude Code rejected it (HTTP ' + res.status + ')');
        return false;
      }
      return true;
    } catch (err) {
      if (kind !== 'probe') fallback(payload, 'Claude Code is no longer listening (' + err.message + ')');
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
__MERMAID__
</body>
</html>
"""


def _filename_base(title: str) -> str:
    """A filesystem-safe stem for the saved file, derived from `title`."""
    stem = "".join(c if c.isalnum() else "-" for c in title.strip().lower())
    stem = "-".join(filter(None, stem.split("-")))
    return stem or "report"


def build_show_page(
    *,
    content: str,
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

    `content` is Markdown, rendered the same way `question` is for `ask`. Save is always
    client-side (a Blob download), regardless of `submit_url`. Acknowledge only actually
    reaches the agent when `submit_url` is set — see `show.run_show` for when that is:
    the shared session (always available) or, on the one-off fallback tab, only if the
    call opted into waiting for it. With no `submit_url`, the Acknowledge button still
    shows and responds, but purely as a local "seen" mark, never sent anywhere.

    `submit_url`/`status_url`/`generation`/`timeout_seconds` mirror `build_page`'s: set
    together when this page is served by a `SessionListener` or a one-shot
    `LoopbackListener`, left at their defaults for a plain unlistened page.
    """
    css = _load_base_css() + _SHOW_ADDENDUM
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
        ack_html = '    <button type="button" id="pp-ack">Acknowledge</button>'

    content_html = render_markdown(content)

    return (
        _SHOW_TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__CONTENT__", content_html)
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(content_html)))
        .replace("__SAVE_BUTTON__", save_html)
        .replace("__ACK_BUTTON__", ack_html)
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__RAW_CONTENT__", _js_string(content))
        .replace("__FILENAME_BASE__", _js_string(_filename_base(title)))
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


def _is_gallery(options: list[dict[str, object]]) -> bool:
    """Two or more image-bearing options make this a gallery, auto-detected the same way
    a long body already auto-collapses into a drawer — nothing for the caller to flag.

    One image alone stays the ordinary layout: comparing needs at least two things next
    to each other, and a single-image option is already well served by the existing
    inline-or-drawer body.
    """
    return sum(1 for option in options if _has_image_body(option)) >= 2


def _render_options(
    options: list[dict[str, object]],
    multi_select: bool,
    *,
    name: str = "pp-option",
    data_step: str | None = None,
    gallery: bool = False,
) -> str:
    if not options:
        return ""
    kind = "checkbox" if multi_select else "radio"
    step_attr = f' data-step="{data_step}"' if data_step is not None else ""
    rows: list[str] = []
    for option in options:
        option_id = html.escape(str(option["id"]), quote=True)
        label = html.escape(str(option["label"]), quote=True)
        body = option.get("body")
        # aria-label, not reliance on the wrapping element, because the long-body case
        # below wraps this input in <summary> rather than <label> — <summary> confers no
        # implicit accessible name the way <label> does, so the input would otherwise
        # announce with none at all.
        select_html = (
            f'<input type="{kind}" name="{name}" data-option-id="{option_id}"{step_attr} '
            f'aria-label="{label}">'
            f'<span class="pp-option-label">{label}</span>'
        )

        if not body:
            rows.append(
                f'    <li class="pp-option"><label class="pp-option-select">'
                f"{select_html}</label></li>"
            )
            continue

        body_html = render_markdown(str(body))
        body_div = f'<div class="pp-option-body">{body_html}</div>'

        # A gallery never drawers its images behind a click — the whole point is seeing
        # them at once — so it always takes the inline branch, long body or not.
        if gallery or not _is_long_body(body_html):
            rows.append(
                f'    <li class="pp-option"><label class="pp-option-select">'
                f"{select_html}</label>{body_div}</li>"
            )
            continue

        # The drawer is the whole row, not a separate control beside it: the row's own
        # <summary> is what opens it, and the checkbox/radio stops its own click from
        # bubbling there (see the script below) so selecting and opening stay
        # independent actions instead of one triggering the other.
        open_attr = " open" if option.get("recommended") else ""
        chevron = '<span class="pp-option-chevron" aria-hidden="true"></span>'
        rows.append(
            f'    <li class="pp-option"><details class="pp-option-drawer" '
            f'data-option-id="{option_id}"{open_attr}>'
            f'<summary class="pp-option-select">{select_html}{chevron}</summary>'
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


def build_page(
    *,
    question: str,
    options: list[dict[str, object]],
    multi_select: bool,
    allow_free_text: bool,
    submit_url: str,
    timeout_seconds: float,
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
    css = _load_base_css() + _ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    free_text = ""
    if allow_free_text:
        free_text = (
            '  <label class="pp-free-text">Anything else'
            '<textarea id="pp-free-text" rows="4" '
            'placeholder="Optional — type a free-text answer"></textarea></label>'
        )

    if options and allow_free_text:
        hint = "Pick an option, write an answer, or both."
    elif options:
        hint = "Pick an option." if not multi_select else "Pick one or more options."
    else:
        hint = "Write an answer."

    gallery = _is_gallery(options)
    question_html = render_markdown(question)
    options_html = _render_options(options, multi_select, gallery=gallery)

    return (
        _TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__MAIN_EXTRA__", ' class="pp-wide"' if gallery else "")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__QUESTION__", question_html)
        .replace("__OPTIONS__", options_html)
        .replace("__FREE_TEXT__", free_text)
        .replace("__HINT__", html.escape(hint, quote=True))
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(question_html, options_html)))
    )


def _render_step(index: int, spec: dict[str, object]) -> str:
    question_html = render_markdown(str(spec.get("question", "")))
    raw_options = spec.get("options")
    options = list(raw_options) if isinstance(raw_options, list) else []
    multi_select = bool(spec.get("multi_select"))
    options_html = _render_options(
        options,
        multi_select,
        name=f"pp-option-step-{index}",
        data_step=str(index),
        gallery=_is_gallery(options),
    )
    free_text_html = ""
    if spec.get("allow_free_text", True):
        free_text_html = (
            f'    <label class="pp-free-text">Anything else'
            f'<textarea id="pp-free-text-{index}" data-step="{index}" rows="3" '
            'placeholder="Optional — type a free-text answer"></textarea></label>'
        )
    # The wizard's JS toggles `pp-step-active` on navigation; the first step starts with
    # it already set so the page shows question one even before the script has run.
    active = " pp-step-active" if index == 0 else ""
    return (
        f'  <section class="pp-step{active}" data-step-index="{index}">\n'
        f'    <div class="pp-question">{question_html}</div>\n'
        f"{options_html}\n"
        f"{free_text_html}\n"
        "  </section>"
    )


def build_multi_page(
    *,
    questions: list[dict[str, object]],
    submit_url: str,
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

    `theme`, `status_url` and `generation` are as `build_page` takes them.
    """
    css = _load_base_css() + _ADDENDUM + _MULTI_ADDENDUM
    if accent is not None:
        css += _accent_css(accent)

    total = len(questions)
    steps_html = "\n".join(_render_step(i, spec) for i, spec in enumerate(questions))
    initial_progress = round(100 / total) if total else 0
    any_gallery = any(
        _is_gallery(options) if isinstance(options := spec.get("options"), list) else False
        for spec in questions
    )

    return (
        _MULTI_TEMPLATE.replace("__CSS__", css)
        .replace("__THEME_ATTR__", _theme_attr(theme))
        .replace("__MAIN_EXTRA__", ' class="pp-wide"' if any_gallery else "")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__STEPS__", steps_html)
        .replace("__INITIAL_LABEL__", html.escape(f"Question 1 of {total}", quote=True))
        .replace("__INITIAL_PROGRESS__", str(initial_progress))
        .replace("__INITIAL_NEXT_LABEL__", "Send answers" if total == 1 else "Next")
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
        .replace("__STEP_COUNT__", str(total))
        .replace("__STATUS_URL__", _js_string(status_url))
        .replace("__GENERATION__", _js_string(generation))
        .replace("__MERMAID__", _mermaid_script(_needs_mermaid(steps_html)))
    )

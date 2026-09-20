"""Assembly of the HTML page one `ask` call opens.

The page is self-contained: styles are inlined, there is no CDN and no network fetch
other than the one POST back to the listener. `base.css` is vendored verbatim from the
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
from importlib import resources

from .markdown import render_markdown

__all__ = ["ACCENT_ENV_VAR", "InvalidAccent", "build_page", "parse_accent"]

# Read by both server.py (to validate once at startup) and ask.py (to resolve the
# colour for each page) — defined here, not in server.py, so ask.py does not have to
# import from the module that already imports it.
ACCENT_ENV_VAR = "PRETTY_PROMPT_ACCENT"

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
     pushes contrast up — near-black text on pure white, a firmer border, and a deeper,
     more saturated accent than the vendored `--ntw-accent`. */
  --ntw-bg: #ffffff;
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

/* Picked over a bordered grid and a zebra-striped card in a three-way visual A/B/C —
   see the "table style" pick in project history. No vertical rules, a single accent
   rule under the header instead of a shaded background. */
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 0.9em; }
th, td { padding: 6px 10px; text-align: left; border: none;
  border-bottom: 1px solid var(--ntw-border); }
thead th { border-bottom: 2px solid var(--ntw-accent); color: var(--ntw-accent);
  font-weight: 600; text-transform: uppercase; font-size: 0.78em; letter-spacing: 0.03em; }
tbody tr:last-child td { border-bottom: none; }

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
.pp-option-chevron::before { content: "\203A"; }
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
#pp-submit { border: 1px solid var(--ntw-accent); background: var(--ntw-accent);
  color: var(--ntw-accent-contrast); }
#pp-cancel { border: 1px solid var(--ntw-border); background: transparent; color: var(--ntw-fg); }
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

_TEMPLATE = """<!doctype html>
<html lang="en">
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

<script>
(function () {
  var SUBMIT_URL = __SUBMIT_URL__;
  var TIMEOUT_SECONDS = __TIMEOUT_SECONDS__;
  var THEME_KEY = 'no-text-wall:theme';

  var form = document.getElementById('pp-form');
  var submitBtn = document.getElementById('pp-submit');
  var cancelBtn = document.getElementById('pp-cancel');
  var status = document.getElementById('pp-status');
  var freeText = document.getElementById('pp-free-text');
  var inputs = Array.prototype.slice.call(document.querySelectorAll('[data-option-id]'));

  // Theme: the root attribute overrides the OS preference, absent follows it. Shared
  // localStorage key with no-text-wall so one toggle covers both surfaces.
  try {
    var stored = localStorage.getItem(THEME_KEY);
    if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
  } catch (e) { /* private mode: fall back to the OS preference */ }
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
  });

  function collect() {
    return {
      selected: inputs.filter(function (i) { return i.checked; })
                      .map(function (i) { return i.dataset.optionId; }),
      free_text: freeText ? freeText.value.trim() : ''
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

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (submitBtn.disabled) return;
    var data = collect();
    freeze('Sending\\u2026');
    if (await send('submit', data)) freeze('Answer sent. You can close this tab.');
    else status.textContent = 'Not sent.';
  });

  cancelBtn.addEventListener('click', async function () {
    freeze('Cancelling\\u2026');
    if (await send('cancel', {})) freeze('Cancelled. You can close this tab.');
    else status.textContent = 'Not sent.';
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
</body>
</html>
"""


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


def _render_options(options: list[dict[str, object]], multi_select: bool) -> str:
    if not options:
        return ""
    kind = "checkbox" if multi_select else "radio"
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
            f'<input type="{kind}" name="pp-option" data-option-id="{option_id}" '
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

        if not _is_long_body(body_html):
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
            f'    <li class="pp-option"><details class="pp-option-drawer"{open_attr}>'
            f'<summary class="pp-option-select">{select_html}{chevron}</summary>'
            f"{body_div}</details></li>"
        )
    return '  <ul class="pp-options">\n' + "\n".join(rows) + "\n  </ul>"


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

    return (
        _TEMPLATE.replace("__CSS__", css)
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__QUESTION__", render_markdown(question))
        .replace("__OPTIONS__", _render_options(options, multi_select))
        .replace("__FREE_TEXT__", free_text)
        .replace("__HINT__", html.escape(hint, quote=True))
        .replace("__SUBMIT_URL__", _js_string(submit_url))
        .replace("__TIMEOUT_SECONDS__", _js_string(timeout_seconds))
    )

"""Assembly of the HTML page one `ask`, `show` or `review` call opens.

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
defines text-wall-breaker's own compact components against the same tokens instead of
composing onto those classes. Keeping `base.css` itself byte-identical is still
deliberate — it stays diffable against its source until this repo forks it properly —
this page just stopped using the part of it that didn't fit.

Markdown arrives here already rendered and escaped by `markdown.py`. Everything else
that comes from the caller (option labels, ids) is escaped at the point of interpolation.
"""

from __future__ import annotations

import functools
import html
import json
import os
import re
import secrets
from bisect import bisect_right
from collections.abc import Callable
from contextvars import ContextVar
from importlib import resources
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from .diff import DiffLine, Gap, gaps_of, parse_unified_diff, rows_with_context
from .intraline import decorated_rows
from .markdown import (
    LONG_QUESTION_CHARS,
    WIDE_QUESTION_CHARS,
    bold_lead,
    first_paragraph,
    render_inline,
    render_markdown,
)
from .render import Block, split_blocks

__all__ = [
    "InvalidAccent",
    "ReviewNote",
    "ReviewPage",
    "asset_base_url_for",
    "build_multi_page",
    "build_page",
    "build_review_page",
    "build_show_page",
    "parse_accent",
    "read_saved_theme",
    "save_theme",
]

# The theme toggle only ever writes localStorage, which is scoped to a `file://` page's
# storage partition — not guaranteed stable across browsers, profiles, or private
# windows, and useless to the reusable session page's own served origin, which is a
# different one again. A tiny state file next to XDG's other per-user state gives the
# preference one home that survives all of those, and that this process itself can read
# back before the first paint instead of flashing the wrong theme and correcting via JS.
_THEME_STATES = ("light", "dark")


def _theme_state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "text-wall-breaker" / "theme"


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


class ReviewNote(TypedDict):
    """An agent note on a review page, already checked against the rows it shows."""

    side: str
    line: int
    text: str
    end_side: NotRequired[str]
    end_line: NotRequired[int]


class ReviewPage(TypedDict):
    """One file of a review page: its diff and what the page shows beside it."""

    path: str
    diff: str
    status: NotRequired[str]
    renamed_from: NotRequired[str | None]
    blurb: NotRequired[str | None]
    lines: NotRequired[list[str] | None]
    notes: NotRequired[list[ReviewNote] | None]
    rendered: NotRequired[str | None]


class InvalidAccent(ValueError):
    """An accent colour that is not `#rrggbb` hex."""


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
        '<div class="twb-progress">'
        f'<span class="twb-progress-label" id="twb-deck-progress-label">Slide 1 of {total}</span>'
        '<span class="twb-progress-track">'
        f'<span class="twb-progress-fill" id="twb-deck-progress-fill" '
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


# The nonce of the page being built: every script tag this module writes carries it, and
# the page's CSP runs no other. Set by `_fresh_nonce` around each page builder.
_nonce: ContextVar[str] = ContextVar("nonce")


def _fresh_nonce[**P](build: Callable[P, str]) -> Callable[P, str]:
    @functools.wraps(build)
    def with_nonce(*args: P.args, **kwargs: P.kwargs) -> str:
        token = _nonce.set(secrets.token_urlsafe(16))
        try:
            return build(*args, **kwargs)
        finally:
            _nonce.reset(token)

    return with_nonce


def _script(body: str) -> str:
    """An inline script of the page being built. `</` is escaped so the body cannot end it."""
    return f'<script nonce="{_nonce.get()}">\n{body.replace("</", "<\\/")}\n</script>'


@_fresh_nonce
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

    `submit_url`/`status_url`/`generation` mirror `build_page`'s: set
    together when this page is served by a `SessionListener` or a one-shot
    `LoopbackListener`, left at their defaults for a plain unlistened page.
    """
    css = (
        _load_asset("base.css")
        + _load_asset("actions.css")
        + _load_asset("show.css")
        + _load_asset("tokens.css")
    )
    if accent is not None:
        css += _accent_css(accent)

    save_html = ""
    if save_button:
        save_html = (
            '    <button type="button" id="twb-save">Save report</button>\n'
            '    <div class="twb-save-menu" id="twb-save-menu" hidden>\n'
            '      <button type="button" id="twb-save-md">as Markdown (.md)</button>\n'
            '      <button type="button" id="twb-save-html">as HTML (.html)</button>\n'
            "    </div>"
        )
    ack_html = ""
    if acknowledge_button:
        ack_html = '      <button type="button" id="twb-ack">Acknowledge</button>'

    asset_base_url = asset_base_url_for(status_url)
    if slides is not None:
        content_html, raw_content = _render_deck(slides, header, footer, asset_base_url)
    else:
        content_html = render_markdown(content, asset_base_url=asset_base_url)
        raw_content = content
    wide = _needs_wide_layout(content_html)

    return _document(
        _load_asset("show.html"),
        {
            "__CSS__": css,
            "__THEME_ATTR__": _theme_attr(theme),
            "__MAIN_EXTRA__": ' class="twb-wide"' if wide else "",
            "__TITLE__": html.escape(title, quote=True),
            "__CONTENT__": content_html,
            "__DECK_SCRIPT__": _deck_script(slides is not None),
            "__MERMAID__": _mermaid_script(_needs_mermaid(content_html)),
            "__SAVE_BUTTON__": save_html,
            "__ACK_BUTTON__": ack_html,
            "__SUBMIT_URL__": _js_string(submit_url),
            "__STATUS_URL__": _js_string(status_url),
            "__GENERATION__": _js_string(generation),
            "__RAW_CONTENT__": _js_string(raw_content),
            "__FILENAME_BASE__": _js_string(_filename_base(title)),
        },
    )


_GAP_STEP = 10


def _render_review_gap(gap: Gap) -> str:
    """The bar standing for a hidden stretch of unchanged lines, expanded by the script.

    A divider with a pill: the middle shows the whole stretch, the step buttons ten lines
    from the hunk above (down) or below (up). A top gap has no hunk above and an end gap
    none below, so each has only the step that makes sense; a stretch of ten or fewer
    lines has no steps since the middle does the same.
    """
    size = gap.size
    many = "" if size > _GAP_STEP else " hidden"
    down = (
        f'<button type="button" class="twb-gap-step" data-step="down"{many} '
        f'aria-label="Show {_GAP_STEP} lines below the hunk above">&darr; {_GAP_STEP}</button>'
        if gap.position != "top"
        else ""
    )
    up = (
        f'<button type="button" class="twb-gap-step" data-step="up"{many} '
        f'aria-label="Show {_GAP_STEP} lines above the hunk below">&uarr; {_GAP_STEP}</button>'
        if gap.position != "end"
        else ""
    )
    label = f"{size} hidden line{'' if size == 1 else 's'}"
    return (
        f'<div class="twb-review-gap" data-position="{gap.position}" '
        f'data-first="{gap.new_first}" data-last="{gap.new_last}" data-delta="{gap.delta}">'
        f'<span class="twb-gap-pill">{down}<button type="button" class="twb-gap-all" '
        f'aria-label="Show all {size} hidden lines">{label}</button>{up}</span></div>'
    )


def _note_label(note: ReviewNote) -> str:
    start = f"{note['side']} {note['line']}"
    if "end_line" not in note:
        return start
    if note.get("end_side") == note["side"]:
        return f"{start}\N{EN DASH}{note['end_line']}"
    return f"{start} \N{RIGHTWARDS ARROW} {note['end_side']} {note['end_line']}"


def _render_review_note(index: int, note: ReviewNote, asset_base_url: str | None) -> str:
    label = html.escape(_note_label(note))
    body = render_markdown(note["text"], asset_base_url=asset_base_url)
    return (
        f'<div class="twb-note is-open" data-note="{index}">'
        f'<div class="twb-note-head"><span class="twb-note-label">&#10022; Agent &middot; '
        f'{label}</span><button type="button" class="twb-note-reply" data-act="reply">Reply'
        f'</button></div><div class="twb-note-body">{body}</div></div>'
    )


def _render_review_row(
    escaped_path: str,
    row: DiffLine,
    text_html: str,
    marks: dict[tuple[str, int], list[int]],
    closings: dict[tuple[str, int], list[int]],
    notes: list[ReviewNote] | None,
    asset_base_url: str | None,
) -> str:
    """One addressable row, with the note chips on it and the notes that end on it."""
    old = str(row.old_line) if row.old_line is not None else ""
    new = str(row.new_line) if row.new_line is not None else ""
    side = row.side or "new"
    line = row.line if row.line is not None else 0
    return (
        f'<div class="twb-review-row-wrap" data-path="{escaped_path}" '
        f'data-side="{side}" data-line="{line}">'
        f'<div class="twb-review-row {row.kind}" role="button" tabindex="0" '
        'aria-label="Select this line to comment">'
        f'<span class="twb-review-gutter">{old}</span>'
        f'<span class="twb-review-gutter">{new}</span>'
        f'<span class="twb-review-text">{text_html}</span>'
        '<span class="twb-review-comment-icon" aria-hidden="true">&#128172;</span>'
        "</div>"
        + "".join(
            f'<button type="button" class="twb-note-mark is-open" data-note="{i}" '
            'aria-expanded="true">&#10022; note</button>'
            for i in marks.get((side, line), [])
        )
        + "</div>"
        + "".join(
            _render_review_note(i, (notes or [])[i], asset_base_url)
            for i in closings.get((side, line), [])
        )
    )


def _render_review_chunks(
    escaped_path: str,
    rows: list[DiffLine],
    blocks: list[Block],
    marks: dict[tuple[str, int], list[int]],
    closings: dict[tuple[str, int], list[int]],
    notes: list[ReviewNote] | None,
    asset_base_url: str | None,
    path: str,
) -> str:
    """The rows of a whole file next to the rendered blocks they belong to, chunk by chunk.

    A chunk is one block of the rendered view, with the rows of its source lines on the
    left. A line outside every block (a blank line) goes with the block before it, a
    deleted row with the block of the line that follows it. A chunk the diff does not
    touch stays folded, in a run of its kind behind one bar, unless it is next to a
    changed one or carries an agent note; the rows are all in the page either way.
    """
    firsts = [block.first for block in blocks]
    anchors: list[int] = []
    following = 1
    for row in reversed(rows):
        following = row.new_line if row.new_line is not None else following
        anchors.append(following)
    anchors.reverse()
    chunk_rows: list[list[int]] = [[] for _ in blocks]
    for index, anchor in enumerate(anchors):
        chunk_rows[max(bisect_right(firsts, anchor) - 1, 0)].append(index)
    colored = decorated_rows(path, rows)
    touched = [
        any(
            rows[i].kind != "ctx"
            or (rows[i].side, rows[i].line) in marks
            or (rows[i].side, rows[i].line) in closings
            for i in members
        )
        for members in chunk_rows
    ]
    shown = [
        touched[i] or (i > 0 and touched[i - 1]) or (i + 1 < len(touched) and touched[i + 1])
        for i in range(len(blocks))
    ]

    def chunk(i: int) -> str:
        body = "".join(
            _render_review_row(
                escaped_path,
                rows[j],
                colored.get(j) or html.escape(rows[j].text) or "&nbsp;",
                marks,
                closings,
                notes,
                asset_base_url,
            )
            for j in chunk_rows[i]
        )
        return (
            f'<div class="twb-chunk"><div class="twb-chunk-diff">{body}</div>'
            f'<div class="twb-rendered">{blocks[i].html}</div></div>'
        )

    parts: list[str] = []
    i = 0
    while i < len(blocks):
        if shown[i]:
            parts.append(chunk(i))
            i += 1
            continue
        end = i
        while end < len(blocks) and not shown[end]:
            end += 1
        count = end - i
        parts.append(
            f'<button type="button" class="twb-fold" aria-expanded="false">'
            f"{count} unchanged block{'' if count == 1 else 's'}</button>"
            f'<div class="twb-fold-body" hidden>{"".join(chunk(k) for k in range(i, end))}</div>'
        )
        i = end
    return "".join(parts)


def _render_review_diff(
    path: str,
    diff: str,
    lines: list[str] | None = None,
    notes: list[ReviewNote] | None = None,
    asset_base_url: str | None = None,
    rendered: str | None = None,
) -> str:
    """One page's diff as `.twb-review-row` rows, each wrapped in `.twb-review-row-wrap`.

    With `rendered` (a Markdown or AsciiDoc page) the page is cut into chunks instead, see
    `_render_review_chunks`.

    `parse_unified_diff` is what turns raw diff text into the (side, line) coordinate a
    comment on that row addresses — see `diff.DiffLine.side`/`.line`. A "hunk" row (the
    `@@ ...` header) is not addressable: there's no single line it could attach to.

    The whole row is the click/keyboard target for selecting it (`role`, `tabindex`; the
    wiring is in `assets/review.html`'s script, which also places a comment's note after its
    last row) — the bubble glyph is a hover affordance only, not a click target itself.

    With `lines` (the whole new-side file, only known for a `repo` source) the stretches
    the diff leaves out get a gap bar, and the text of each travels in one JSON script
    element, in gap order, for the script to turn into rows when the bar is clicked.

    `notes` are the agent's read-only remarks, each anchored like a comment on rows the
    diff shows (`Page.validated` checks that). A note opens inline under its last row; a
    chip on its first row folds it. They start open: the reasoning is what the operator
    is there to read.
    """
    escaped_path = html.escape(path, quote=True)
    rows: list[DiffLine] = parse_unified_diff(diff)
    gaps = gaps_of(diff, len(lines)) if lines is not None else []
    gap_before = {gap.hunk: gap for gap in gaps if gap.position != "end"}
    marks: dict[tuple[str, int], list[int]] = {}
    closings: dict[tuple[str, int], list[int]] = {}
    for index, note in enumerate(notes or []):
        start = (note["side"], note["line"])
        end = (
            (note["end_side"], note["end_line"])
            if "end_side" in note and "end_line" in note
            else start
        )
        marks.setdefault(start, []).append(index)
        closings.setdefault(end, []).append(index)
    parts = [f'<div class="twb-review-diff" data-path="{escaped_path}">']
    blocks = split_blocks(rendered) if rendered else []
    if blocks:
        # With the file text every line gets a row, so the gap bars have nothing left to
        # hide. Without it (a `pages` source) the rows are the hunks' only.
        whole = (
            rows_with_context(diff, lines)
            if lines is not None
            else [row for row in rows if row.kind != "hunk"]
        )
        parts.append(
            _render_review_chunks(
                escaped_path, whole, blocks, marks, closings, notes, asset_base_url, path
            )
        )
        parts.append("</div>")
    else:
        hunk = -1
        colored = decorated_rows(path, rows)
        for index, row in enumerate(rows):
            text_html = colored.get(index) or html.escape(row.text) or "&nbsp;"
            if row.kind == "hunk":
                hunk += 1
                if hunk in gap_before:
                    parts.append(_render_review_gap(gap_before[hunk]))
                parts.append(f'<div class="twb-review-row hunk">{text_html}</div>')
                continue
            parts.append(
                _render_review_row(
                    escaped_path, row, text_html, marks, closings, notes, asset_base_url
                )
            )
        parts.extend(_render_review_gap(gap) for gap in gaps if gap.position == "end")
        if gaps and lines is not None:
            # `<` is escaped so a file containing `</script>` cannot end the element early.
            payload = json.dumps([lines[gap.new_first - 1 : gap.new_last] for gap in gaps])
            escaped = payload.replace("<", "\\u003c")
            parts.append(
                f'<script type="application/json" class="twb-review-lines">{escaped}</script>'
            )
        parts.append("</div>")
    if notes:
        count = len(notes)
        parts.insert(
            0,
            '<div class="twb-notes-bar" data-state="shown">'
            f'<span class="twb-switch-label">&#10022; {count} note{"" if count == 1 else "s"} '
            'from the agent</span><span class="twb-switch">'
            '<button type="button" data-all="0">Hide all</button>'
            '<button type="button" data-all="1">Show all</button></span></div>',
        )
    return "".join(parts)


_LEADING_H1 = re.compile(r"\A\s*<h1>(.*?)</h1>(.*)\Z", re.DOTALL)


def _plural(count: int, noun: str) -> str:
    return f"<b>{count}</b> {noun}{'' if count == 1 else 's'}"


def _render_review_header(
    header: str | None, pages: list[ReviewPage], asset_base_url: str | None
) -> str:
    """The top of the page: one slim row, the agent's header folded under "Details".

    The row holds the header's leading heading (or "Review") and chips with the file,
    comment and note counts; the comment count is kept live by the page script. The rest
    of the header is behind a
    fold when there is a heading to stand in for it, and open when there is not, so nothing
    is hidden without a title to say what it is.
    """
    body = render_markdown(header, asset_base_url=asset_base_url) if header else ""
    lead = _LEADING_H1.match(body)
    title = (
        f'<h1 class="twb-head-title">{lead.group(1)}</h1>'
        if lead
        else ('<span class="twb-head-title">Review</span>')
    )
    rest = lead.group(2) if lead else body
    notes = sum(len(n) for page in pages if isinstance(n := page.get("notes"), list))
    chips = (
        f'<span class="twb-chip">{_plural(len(pages), "file")}</span>'
        f'<span class="twb-chip" id="twb-chip-comments">{_plural(0, "comment")}</span>'
        + (f'<span class="twb-chip">{_plural(notes, "agent note")}</span>' if notes else "")
    )
    fold = ""
    if rest.strip():
        opened = "" if lead else " open"
        fold = (
            f'<details class="twb-head-details"{opened}><summary>Details</summary>'
            f'<div class="twb-content">{rest}</div></details>'
        )
    return (
        f'<header class="twb-head"><div class="twb-head-row">{title}'
        f'<span class="twb-chips">{chips}</span></div>{fold}</header>'
    )


def _render_review_pages(
    pages: list[ReviewPage], asset_base_url: str | None, *, with_files: bool
) -> str:
    """The deck of `.deck-slide`s for a `review` call — one per page, in order.

    Same free-navigation deck mechanics as `_render_deck` (`show`'s slides), reused here
    via base.css's `.deck-slide`/`.deck-nav` — but each slide is an addressable diff
    (`_render_review_diff`) rather than arbitrary Markdown, and the progress label reads
    "Page", not "Slide" — see `_review_deck_script`.
    """
    # A single page has nothing to navigate between: the file list would be inert chrome
    # around the one diff, so it is skipped rather than rendered and left doing nothing.
    files_html = _render_review_files(pages) if with_files else ""
    slide_parts = []
    for index, page in enumerate(pages):
        active = " is-active" if index == 0 else ""
        path = str(page["path"])
        blurb = page.get("blurb")
        blurb_md = render_markdown(str(blurb), asset_base_url=asset_base_url) if blurb else ""
        blurb_html = f'<div class="twb-blurb">{blurb_md}</div>' if blurb else ""
        folder, _, name = path.rpartition("/")
        path_html = (
            _status_badge(page)
            + f'<span class="twb-path-dir">{html.escape(folder + "/" if folder else "")}</span>'
            f'<span class="twb-path-file">{html.escape(name)}</span>'
        )
        page_lines = page.get("lines")
        page_notes = page.get("notes")
        rendered = page.get("rendered")
        diff_html = _render_review_diff(
            path,
            str(page["diff"]),
            page_lines if isinstance(page_lines, list) else None,
            page_notes if isinstance(page_notes, list) else None,
            asset_base_url,
            rendered if isinstance(rendered, str) else None,
        )
        if 'class="twb-chunk"' in diff_html:
            diff_html = f'<div class="twb-views">{diff_html}</div>'
        note_id = f"twb-page-note-{index}"
        slide_parts.append(
            f'<section class="deck-slide card{active}">'
            f'<div class="twb-review-path">{path_html}</div>'
            f"{blurb_html}"
            f"{diff_html}"
            f'<label class="twb-page-note-label" for="{note_id}">'
            "Note on this file (optional)</label>"
            f'<textarea class="twb-page-note" id="{note_id}" '
            f'data-path="{html.escape(path, quote=True)}" '
            'placeholder="Anything about this file as a whole, beyond the line comments">'
            "</textarea>"
            "</section>"
        )
    return files_html + "\n".join(slide_parts)


# From this many files on the list gets a filter; below it the list fits the screen anyway.
_FILTER_FROM = 8


_STATUS_WORDS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}


def _status_badge(page: ReviewPage) -> str:
    """The letter that says how the file changed (see `diff.file_status`), or "" if unknown.

    A rename names its old path in the badge's tooltip.
    """
    status = page.get("status")
    if not isinstance(status, str) or status not in _STATUS_WORDS:
        return ""
    title = _STATUS_WORDS[status]
    if status == "R" and page.get("renamed_from"):
        title += f" from {page['renamed_from']}"
    return (
        f'<span class="twb-status twb-status-{status}" '
        f'title="{html.escape(title, quote=True)}">{status}</span>'
    )


def _render_review_files(pages: list[ReviewPage]) -> str:
    """The file list of a multi-page `review`: a sidebar, and below 900px a bar + drawer.

    Counts and the note mark start at zero/hidden and are kept current by
    `_review_files_script` from the comment and note boxes themselves, so the list never
    holds a second copy of what the operator typed. Fixed-position, so where it sits in
    the document does not matter.
    """
    rows = []
    for index, page in enumerate(pages):
        path = str(page["path"])
        directory, _, name = path.rpartition("/")
        shown_directory = html.escape(directory + "/" if directory else "./")
        current = " is-current" if index == 0 else ""
        # The filter box matches the title, so a renamed file is found by its old path too.
        old_path = page.get("renamed_from")
        row_title = f"{path} (renamed from {old_path})" if old_path else path
        rows.append(
            f'<button type="button" class="twb-file{current}" data-index="{index}" '
            f'title="{html.escape(row_title, quote=True)}">'
            f"{_status_badge(page)}"
            '<span class="twb-file-paths">'
            f'<span class="twb-file-name">{html.escape(name)}</span>'
            f'<span class="twb-file-dir">{shown_directory}</span>'
            "</span>"
            '<span class="twb-file-note" title="Note on this file" hidden>&#9998;</span>'
            '<span class="twb-file-count is-zero" title="0 comments">0</span>'
            "</button>"
        )
    tools = (
        '<div class="twb-files-tools">'
        '<input type="search" id="twb-files-filter" aria-label="Filter files" '
        'placeholder="Filter files&hellip;">'
        '<label><input type="checkbox" id="twb-files-marked"> only with comments or a note'
        "</label></div>"
        '<div id="twb-files-empty" hidden>No file matches.</div>'
        if len(pages) >= _FILTER_FROM
        else ""
    )
    return (
        '<nav id="twb-files" aria-label="Files">'
        '<h3 class="twb-files-head">Files <span id="twb-files-total">0 comments</span></h3>'
        + tools
        + "".join(rows)
        + "</nav>"
        '<div id="twb-files-backdrop" hidden></div>'
        '<div id="twb-filebar">'
        '<button type="button" id="twb-files-toggle" aria-expanded="false" '
        'aria-controls="twb-files">Files</button>'
        '<span id="twb-filebar-name"></span>'
        '<span id="twb-filebar-pos"></span>'
        '<button type="button" data-file-step="-1" aria-label="Previous file">&larr;</button>'
        '<button type="button" data-file-step="1" aria-label="Next file">&rarr;</button>'
        "</div>"
    )


def _review_files_script(needed: bool) -> str:
    """Switching files and keeping the file list's counts current.

    Loaded only for more than one page, where `_render_review_files` rendered the list.
    The counts are read from the comment notes and page note boxes whenever a comment
    changes or a note is typed, so they stay right without the list owning any state.
    """
    if not needed:
        return ""
    return _script(_load_asset("review_files.js"))


@_fresh_nonce
def build_review_page(
    *,
    pages: list[ReviewPage],
    submit_url: str,
    header: str | None = None,
    title: str = "Claude Code",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
    live: str = "off",
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

    `submit_url`/`status_url`/`generation` mirror `build_page`'s — see
    there.

    `live` is "off" (no live session possible: a one-off tab), "offer" (the file list
    offers to start one) or "on" (the page belongs to a live session); see
    `assets/live.js` and ADR 0004. Whenever it is not "off" the file list is shown even
    for one file, since the session card lives at its top.
    """
    with_files = len(pages) > 1 or live != "off"
    css = (
        _load_asset("base.css")
        + _load_asset("actions.css")
        + _load_asset("review.css")
        + _load_asset("tokens.css")
        + _load_asset("export.css")
    )
    if live != "off":
        css += _load_asset("live.css")
    if accent is not None:
        css += _accent_css(accent)

    asset_base_url = asset_base_url_for(status_url)
    header_html = _render_review_header(header, pages, asset_base_url)
    content_html = _render_review_pages(pages, asset_base_url, with_files=with_files)
    live_script = _script(_load_asset("live.js")) if live != "off" else ""

    return _document(
        _load_asset("review.html"),
        {
            "__CSS__": css,
            "__THEME_ATTR__": _theme_attr(theme),
            "__TITLE__": html.escape(title, quote=True),
            "__HEADER_BLOCK__": header_html,
            "__CONTENT__": content_html,
            "__BODY_CLASS__": ' class="twb-has-files"' if with_files else "",
            "__DECK_SCRIPT__": _review_files_script(with_files),
            "__LIVE_SCRIPT__": live_script,
            "__EXPORT_SCRIPT__": _export_script(),
            "__MERMAID__": _mermaid_script(_needs_mermaid(content_html, header_html)),
            "__SUBMIT_URL__": _js_string(submit_url),
            "__STATUS_URL__": _js_string(status_url),
            "__GENERATION__": _js_string(generation),
            "__LIVE_MODE__": _js_string(live),
        },
    )


_PLACEHOLDER = re.compile(r"__[A-Z_]+__")


def _fill(template: str, values: dict[str, str]) -> str:
    """`template` with each placeholder replaced by its value, in one pass.

    Chained `str.replace` calls rescan text already inserted: a diff of this very file
    holds the placeholders as text, and got scripts injected into its rows.
    """
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(0), m.group(0)), template)


def _document(template: str, values: dict[str, str]) -> str:
    """A page's final HTML: `template` filled, then locked down by a CSP.

    Only script tags carrying this page's nonce run: the template's own (`__NONCE__`) and
    those `_script` wrote. Text from the agent is never one of them, whatever it smuggles in.
    Images: the page's own origin (`/asset`), `file:` (a page opened from disk) and
    inline data. Requests: only the listener the page's submit URL names, or none.
    """
    nonce = _nonce.get()
    submit_url = json.loads(values.get("__SUBMIT_URL__", "null"))
    connect = "'none'"
    if isinstance(submit_url, str):
        parts = urlsplit(submit_url)
        connect = f"{parts.scheme}://{parts.netloc}"
    policy = "; ".join(
        (
            "default-src 'none'",
            f"script-src 'nonce-{nonce}' {_MERMAID_SRC}",
            "style-src 'unsafe-inline'",
            "img-src 'self' data: blob: file:",
            f"connect-src {connect}",
            "base-uri 'none'",
            "form-action 'none'",
        )
    )
    head = (
        f'<head>\n<meta http-equiv="Content-Security-Policy" content="{policy}">\n'
        '<meta name="referrer" content="no-referrer">'
    )
    return _fill(template, {**values, "__NONCE__": nonce}).replace("<head>", head, 1)


def _glossary_assets(glossary: list[dict[str, str]] | None) -> tuple[str, str]:
    """The stylesheet and `<script>` defining glossary terms on hover, or two empty strings."""
    if not glossary:
        return "", ""
    return (
        _load_asset("glossary.css"),
        _script(
            "window.__TWB_GLOSSARY__ = " + _js_string(glossary) + ";\n" + _load_asset("glossary.js")
        ),
    )


# Page assets, all under assets/. Stylesheets, in the order a page concatenates them after
# base.css (so each overrides rather than fights it):
# - actions.css: the action row of every page type, one stylesheet so the four cannot
#   drift. Three looks only: solid accent (the one primary action), outlined (every other
#   action), dashed and transparent (disabled, never a fill, so it cannot pass for an
#   outlined button). The row is split: leave actions (Cancel, Stop here) left, forward
#   actions (Back, primary) in `.twb-actions-end` right.
# - ask.css: ask's own components, sized for one short question rather than a long report;
#   see the module docstring for why this stopped composing onto base.css's `.card` and
#   `.theme-toggle`. multi.css is layered onto it (not merged) for the wizard page only:
#   a progress bar in place of the single question.
# - export.css: the export button and popover of ask, the wizard and review (export.js).
# - show.css and review.css deliberately duplicate ask.css's palette, font, actions and
#   fallback rules rather than share them, so a page does not carry rules for elements it
#   never renders. review.css's own pieces (from `.twb-review-path` down) are a whole-row
#   comment target per diff line and the overall-note box; see CONTEXT.md's "Review".
# Page templates: ask.html, multi.html (a wizard: one `.twb-step` visible at a time, toggled
# by `.twb-step-active` and not by removing the others from the DOM, so every step's markup
# is still present for a static read), show.html and review.html. show.html copies
# ask.html's fallback box, heartbeat and reuse-polling script, since a listener-backed
# show page needs the same abandonment-heartbeat and tab-reuse behaviour.
# Script bodies: deck.js, review_files.js and mermaid.js, wrapped in `<script>` here.
# Read once, when the module loads: a server started from a checkout keeps its code in
# memory, and a template edited on disk later (a branch switch, say) would otherwise be
# filled by code that does not know its placeholders, leaving the page's script broken.
_ASSETS = {
    entry.name: entry.read_text(encoding="utf-8")
    for entry in resources.files(__package__).joinpath("assets").iterdir()
    if entry.is_file() and entry.name.endswith((".html", ".css", ".js", ".svg"))
}


def _load_asset(name: str) -> str:
    return _ASSETS[name]


def parse_accent(value: str) -> tuple[int, int, int]:
    """Parse a `#rrggbb` (or `rrggbb`) hex colour into its RGB components.

    Raises `InvalidAccent` naming the value, so a bad `--accent` fails with a message
    pointing at the setting rather than a bare `ValueError` from inside a string slice.
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


def asset_base_url_for(status_url: str | None) -> str | None:
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
    name: str = "twb-option",
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
        # Inline rendering only: block syntax (a leading `>`, a heading) has nowhere to go
        # in a one-line title, so it stays literal text instead of breaking the row.
        label_html = render_inline(raw_label, asset_base_url=asset_base_url)
        body = option.get("body")
        # aria-label, not reliance on the wrapping element, because the long-body case
        # below wraps this input in <summary> rather than <label> — <summary> confers no
        # implicit accessible name the way <label> does, so the input would otherwise
        # announce with none at all. Plain-escaped, not label_html: an aria-label is read
        # as text, not markup, so it must stay free of HTML tags.
        checked_attr = " checked" if option.get("selected") else ""
        body_html = render_markdown(str(body), asset_base_url=asset_base_url) if body else ""
        # A gallery never drawers its images behind a click — the whole point is seeing
        # them at once — so it always takes the inline branch, long body or not.
        drawer = bool(body) and not gallery and _is_long_body(body_html)

        # The row is the drawer's <summary>, but only the Details pill opens it; the
        # page's script turns any other click on the row into selecting the option.
        tags = (
            '<span class="twb-option-recommended">Recommended</span>'
            if option.get("recommended")
            else ""
        ) + (
            f'<span class="twb-option-details" aria-hidden="true">Details{_CHEVRON_SVG}</span>'
            if drawer
            else ""
        )
        # The tag and the Details pill float inside the label, ahead of its text, so a
        # long label wraps under them instead of being squeezed into a column beside them.
        tags_html = f'<span class="twb-option-tags">{tags}</span>' if tags else ""
        select_html = (
            f'<input type="{kind}" name="{name}" data-option-id="{option_id}"{step_attr}'
            f'{checked_attr} aria-label="{label}">'
            f'<span class="twb-option-label">{tags_html}{label_html}</span>'
        )

        # Inside the select row (the <label> or the drawer's <summary>) rather than a
        # sibling of the body: a closed <details> shows only its <summary>, and the
        # caveat has to stay visible without opening anything. Inline rendering only, so
        # block syntax in a caveat stays literal text instead of breaking the row.
        caveat = option.get("caveat")
        caveat_inline = render_inline(str(caveat), asset_base_url=asset_base_url) if caveat else ""
        caveat_html = f'<div class="twb-option-caveat">{caveat_inline}</div>' if caveat else ""

        if not body:
            rows.append(
                f'    <li class="twb-option"><label class="twb-option-select">'
                f"{select_html}{caveat_html}</label></li>"
            )
            continue

        body_div = f'<div class="twb-option-body">{body_html}</div>'

        if not drawer:
            rows.append(
                f'    <li class="twb-option"><label class="twb-option-select">'
                f"{select_html}{caveat_html}</label>{body_div}</li>"
            )
            continue

        open_attr = " open" if option.get("recommended") else ""
        rows.append(
            f'    <li class="twb-option"><details class="twb-option-drawer" '
            f'data-option-id="{option_id}"{open_attr}>'
            f'<summary class="twb-option-select">{select_html}{caveat_html}</summary>'
            f"{body_div}</details></li>"
        )
    ul_class = "twb-options twb-options-gallery" if gallery else "twb-options"
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
# Subresource integrity for exactly that file; its sha256 matches jsDelivr's own listing.
_MERMAID_INTEGRITY = "sha384-EOXBFmc3gx5mb+vn0vPvvGqACToJD24hhacX5Yx+8NUUQrHIle/Qi5Bg9o3zKwW2"


def _needs_mermaid(*fragments: str) -> bool:
    """True if any rendered fragment carries a ```mermaid fence (see markdown.py)."""
    return any('class="mermaid"' in fragment for fragment in fragments)


def _needs_wide_layout(*fragments: str) -> bool:
    """True if any rendered fragment carries a ```diff/code fence or a table.

    These are exactly the content shapes that hit their own horizontal scrollbar
    (`.twb-diff`/`pre.code-block`/`table` all set `overflow-x: auto` or scroll their
    container) well before an ordinary question or report would — `review`'s page hit
    this with a fixed max-width that left most of the window unused (see its own
    width fix). Widened the same way a gallery question already is (`_is_gallery`),
    not by a length threshold: a merely long paragraph doesn't benefit from extra
    width the way a wide structured block does — prose gets harder to read wider, not
    easier.
    """
    return any(
        'class="twb-diff"' in fragment or 'class="code-block"' in fragment or "<table" in fragment
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
    return (
        f'<script nonce="{_nonce.get()}" src="{_MERMAID_SRC}" integrity="{_MERMAID_INTEGRITY}" '
        'crossorigin="anonymous"></script>\n' + _script(_load_asset("mermaid.js"))
    )


def _deck_script(needed: bool) -> str:
    """The `<script>` driving `.deck-slide` prev/next/keyboard nav, or "" if not a deck.

    Loaded only when `build_show_page` was given `slides` — a plain-content page has no
    `.deck-slide` elements for this to act on. The nav mechanics (prev/next, keyboard,
    wraparound) are the same ones exercised in no-text-wall's `TEMPLATES.md` `deck`
    template; the progress bar it drives instead of that template's plain "N / total"
    span reuses `ask`'s own wizard-progress widget (`.twb-progress`) for visual
    consistency across text-wall-breaker's two multi-step surfaces.
    """
    if not needed:
        return ""
    return _script(_load_asset("deck.js"))


def _export_script() -> str:
    """The `<script>` of the export button, on every page that takes an answer."""
    return _script(_load_asset("export.js"))


def _ask_live_assets(live: str) -> tuple[str, str]:
    """The stylesheet and `<script>` of the live discussion, or two empty strings if "off".

    The bubble icon is "Thought bubble" by SeregaCthtuf (game-icons.net, CC BY 3.0); its
    file carries the attribution, and so does NOTICE.
    """
    if live == "off":
        return "", ""
    script = _load_asset("ask_live.js").replace(
        "__ICON__", json.dumps(_load_asset("thought-bubble.svg"))
    )
    return _load_asset("ask_live.css"), _script(script)


@_fresh_nonce
def build_page(
    *,
    question: str,
    options: list[dict[str, object]],
    multi_select: bool,
    allow_free_text: bool,
    free_text_prefill: str = "",
    submit_url: str,
    title: str = "Claude Code is asking",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
    live: str = "off",
    glossary: list[dict[str, str]] | None = None,
) -> str:
    """Return the complete HTML document for one `ask` call.

    `accent` is a `#rrggbb` hex colour overriding the built-in green, or None to use it
    as-is. Raises `InvalidAccent` if set and unparseable; `--accent` is validated when
    the server starts, so a value from there never raises mid-call.

    `theme` is `"light"`, `"dark"`, or None to follow the OS preference until the page's
    own toggle is used — see `read_saved_theme`.

    `status_url` and `generation` are only set when this page is served by a
    `listener.SessionListener` for tab reuse: once answered, the page polls `status_url`
    and reloads the moment the served generation moves past `generation`, landing on the
    next question without a new tab. Leave both at their defaults for the ordinary
    one-shot, `file://`-opened page, where there is nothing to poll.

    `live` is "off" (no discussion possible: a one-off tab), "offer" (the operator may
    start one from an option) or "on" (the page belongs to a live session); see
    `assets/ask_live.js` and ADR 0005.
    """
    live_css, live_script = _ask_live_assets(live)
    glossary_css, glossary_script = _glossary_assets(glossary)
    live_script += glossary_script
    css = (
        _load_asset("base.css")
        + _load_asset("actions.css")
        + _load_asset("ask.css")
        + _load_asset("tokens.css")
        + _load_asset("export.css")
        + live_css
        + glossary_css
    )
    if accent is not None:
        css += _accent_css(accent)

    free_text = ""
    if allow_free_text:
        free_text = (
            '  <label class="twb-free-text">Anything else'
            '<textarea id="twb-free-text" rows="4" '
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
    asset_base_url = asset_base_url_for(status_url)
    question_html, question_class = _question_html(question, asset_base_url)
    options_html = _render_options(
        options, multi_select, gallery=gallery, asset_base_url=asset_base_url
    )
    wide = (
        gallery
        or len(question) > WIDE_QUESTION_CHARS
        or _needs_wide_layout(question_html, options_html)
    )

    question_block = (
        f'  <section class="twb-question{question_class}">\n{question_html}\n  </section>'
    )
    return _document(
        _load_asset("ask.html"),
        {
            "__CSS__": css,
            "__THEME_ATTR__": _theme_attr(theme),
            "__MAIN_EXTRA__": ' class="twb-wide"' if wide else "",
            "__TITLE__": html.escape(title, quote=True),
            "__QUESTION_BLOCK__": question_block,
            "__OPTIONS__": options_html,
            "__FREE_TEXT__": free_text,
            "__HINT__": html.escape(hint, quote=True),
            "__SUBMIT_URL__": _js_string(submit_url),
            "__STATUS_URL__": _js_string(status_url),
            "__GENERATION__": _js_string(generation),
            "__LIVE_MODE__": _js_string(live),
            "__LIVE_SCRIPT__": live_script,
            "__EXPORT_SCRIPT__": _export_script(),
            "__MERMAID__": _mermaid_script(_needs_mermaid(question_html, options_html)),
        },
    )


def _question_html(question: str, asset_base_url: str | None) -> tuple[str, str]:
    """The question's HTML and the class marking a long one, so only its lead stays bold."""
    if len(first_paragraph(question)) <= LONG_QUESTION_CHARS:
        return render_markdown(question, asset_base_url=asset_base_url), ""
    leading = bold_lead(question) or question
    return render_markdown(leading, asset_base_url=asset_base_url), " twb-long"


def _render_step(index: int, spec: dict[str, object], asset_base_url: str | None = None) -> str:
    question_html, question_class = _question_html(str(spec.get("question", "")), asset_base_url)
    raw_options = spec.get("options")
    options = list(raw_options) if isinstance(raw_options, list) else []
    multi_select = bool(spec.get("multi_select"))
    rank = spec.get("rank")
    if rank is True or isinstance(rank, list):
        prefill = spec.get("rank_prefill")
        options_html = _render_rank(
            index,
            options,
            rank,
            prefill if isinstance(prefill, dict) else {},
            asset_base_url,
        )
    else:
        options_html = _render_options(
            options,
            multi_select,
            name=f"twb-option-step-{index}",
            data_step=str(index),
            gallery=_is_gallery(options),
            asset_base_url=asset_base_url,
        )
    free_text_html = ""
    if spec.get("allow_free_text", True):
        free_text_html = (
            f'    <label class="twb-free-text">Anything else'
            f'<textarea id="twb-free-text-{index}" data-step="{index}" rows="3" '
            'placeholder="Optional — type a free-text answer">\n'
            f"{html.escape(str(spec.get('free_text') or ''))}</textarea></label>"
        )
    # The wizard's JS toggles `twb-step-active` on navigation; the first step starts with
    # it already set so the page shows question one even before the script has run.
    active = " twb-step-active" if index == 0 else ""
    question_block = f'    <div class="twb-question{question_class}">{question_html}</div>'
    return (
        f'  <section class="twb-step{active}" data-step-index="{index}">\n'
        f"{question_block}\n"
        f"{options_html}\n"
        f"{free_text_html}\n"
        "  </section>"
    )


_RANK_MOVES = (
    '<button type="button" class="twb-rank-move" data-move="-1" aria-label="{up}">&#8593;</button>'
    '<button type="button" class="twb-rank-move" data-move="1" aria-label="{down}">&#8595;</button>'
)


def _render_rank(
    index: int,
    options: list[dict[str, object]],
    rank: object,
    prefill: dict[str, object],
    asset_base_url: str | None,
) -> str:
    """A rank question's widget: one ordered list, or tier rows plus Unsorted.

    `prefill` is a resumed answer (`order`, or `tiers`); the page script reads the
    answer back from the DOM order, so the markup is the only state.
    """
    by_id = {str(option.get("id")): option for option in options}
    ids = list(by_id)

    def label(option_id: str) -> str:
        # Inline Markdown, as an option label of a pick question renders.
        return render_inline(str(by_id[option_id].get("label", "")), asset_base_url=asset_base_url)

    if not isinstance(rank, list):
        sent = prefill.get("order")
        order = [i for i in sent if i in by_id] if isinstance(sent, list) else []
        order = list(dict.fromkeys(order))
        sent = prefill.get("excluded")
        excluded = [i for i in sent if i in by_id] if isinstance(sent, list) else []
        excluded = [i for i in dict.fromkeys(excluded) if i not in order]
        order += [i for i in ids if i not in order and i not in excluded]
        items = []
        # The cut line is a list entry of its own: rows after it are left out.
        cut = (
            '<li class="twb-rank-cut" draggable="true">'
            '<button type="button" class="twb-rank-move" data-move="-1" '
            'aria-label="Move the cut line up">&#8593;</button>'
            "<span>left out below</span>"
            '<button type="button" class="twb-rank-move" data-move="1" '
            'aria-label="Move the cut line down">&#8595;</button></li>'
        )
        for option_id in [*order, None, *excluded]:
            if option_id is None:
                items.append(cut)
                continue
            option = by_id[option_id]
            body = option.get("body")
            caveat = option.get("caveat")
            body_html = ""
            if body:
                body_html = render_markdown(str(body), asset_base_url=asset_base_url)
                body_html = f'<div class="twb-rank-body">{body_html}</div>'
            caveat_html = ""
            if caveat:
                caveat_html = render_inline(str(caveat), asset_base_url=asset_base_url)
                caveat_html = f'<div class="twb-rank-caveat">{caveat_html}</div>'
            items.append(
                f'<li class="twb-rank-item" draggable="true" '
                f'data-option-id="{html.escape(option_id, quote=True)}">'
                '<span class="twb-rank-grip" aria-hidden="true">&#10303;</span>'
                '<span class="twb-rank-pos" aria-hidden="true"></span>'
                f'<div class="twb-rank-text"><div class="twb-option-label">{label(option_id)}</div>'
                f"{body_html}{caveat_html}</div>"
                + _RANK_MOVES.format(up="Move up", down="Move down")
                + "</li>"
            )
        return f'  <ol class="twb-rank" data-step="{index}">\n' + "\n".join(items) + "\n  </ol>"

    tiers = [str(name) for name in rank]
    sent_tiers = prefill.get("tiers")
    sent_tiers = sent_tiers if isinstance(sent_tiers, dict) else {}
    placed: set[str] = set()
    members: dict[str, list[str]] = {}
    for name in tiers:
        sent = sent_tiers.get(name)
        sent = sent if isinstance(sent, list) else []
        members[name] = [i for i in dict.fromkeys(sent) if i in by_id and i not in placed]
        placed.update(members[name])
    members[""] = [i for i in ids if i not in placed]

    rows = []
    for name in [*tiers, ""]:
        chips = "".join(
            f'<span class="twb-tier-chip" draggable="true" '
            f'data-option-id="{html.escape(option_id, quote=True)}">'
            f'<span class="twb-option-label">{label(option_id)}</span>'
            + _RANK_MOVES.format(up="Move up a tier", down="Move down a tier")
            + "</span>"
            for option_id in members[name]
        )
        shown = html.escape(name) if name else "Unsorted"
        extra = "" if name else " twb-tier-unsorted"
        rows.append(
            f'<div class="twb-tier{extra}" data-tier="{html.escape(name, quote=True)}">'
            f'<div class="twb-tier-name">{shown}</div>'
            f'<div class="twb-tier-items">{chips}</div></div>'
        )
    return f'  <div class="twb-tiers" data-step="{index}">\n' + "\n".join(rows) + "\n  </div>"


def build_step_fragments(
    questions: list[dict[str, object]], asset_base_url: str | None = None
) -> list[str]:
    """One `<section class="twb-step">` per question, what `build_multi_page` puts in the page.

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
        return f'  <div class="twb-header">{header_html}</div>'
    lists = header_html.count("<ul>") + header_html.count("<ol>")
    count = ""
    if lists == 1:
        count = f' <span class="twb-header-count">{header_html.count("<li>")} items</span>'
    return (
        f'  <details class="twb-header"><summary>Context{count}</summary>'
        f'<div class="twb-header-body">{header_html}</div></details>'
    )


@_fresh_nonce
def build_multi_page(
    *,
    questions: list[dict[str, object]],
    submit_url: str,
    header: str = "",
    title: str = "Claude Code is asking",
    accent: str | None = None,
    theme: str | None = None,
    status_url: str | None = None,
    generation: int = 0,
    live: str = "off",
    glossary: list[dict[str, str]] | None = None,
) -> str:
    """Return the complete HTML document for an `ask` call carrying several questions.

    A sibling of `build_page` rather than a generalisation of it, so the
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

    `theme`, `status_url`, `generation` and `live` are as `build_page` takes them.
    """
    live_css, live_script = _ask_live_assets(live)
    glossary_css, glossary_script = _glossary_assets(glossary)
    live_script += glossary_script
    css = (
        _load_asset("base.css")
        + _load_asset("actions.css")
        + _load_asset("ask.css")
        + _load_asset("tokens.css")
        + _load_asset("multi.css")
        + _load_asset("export.css")
        + live_css
        + glossary_css
    )
    if accent is not None:
        css += _accent_css(accent)

    total = len(questions)
    asset_base_url = asset_base_url_for(status_url)
    steps_html = "\n".join(build_step_fragments(questions, asset_base_url=asset_base_url))
    header_html = _render_header(header, asset_base_url=asset_base_url)
    segments_html = "".join(
        f'<button type="button" class="twb-seg{" twb-seg-cur" if i == 0 else ""}" '
        f'data-step="{i}" aria-label="Question {i + 1}" disabled></button>'
        for i in range(total)
    )
    initial_label = f"Question 1 of {total}"
    initial_next = "Next" if total > 1 else "Send answers"
    any_gallery = any(
        _is_gallery(options) if isinstance(options := spec.get("options"), list) else False
        for spec in questions
    )
    wide = (
        any_gallery
        or any(len(str(spec.get("question", ""))) > WIDE_QUESTION_CHARS for spec in questions)
        or _needs_wide_layout(steps_html, header_html)
    )

    return _document(
        _load_asset("multi.html"),
        {
            "__CSS__": css,
            "__THEME_ATTR__": _theme_attr(theme),
            "__MAIN_EXTRA__": ' class="twb-wide"' if wide else "",
            "__TITLE__": html.escape(title, quote=True),
            "__HEADER__": header_html,
            "__STEPS__": steps_html,
            "__INITIAL_LABEL__": html.escape(initial_label, quote=True),
            "__SEGMENTS__": segments_html,
            "__INITIAL_NEXT_LABEL__": initial_next,
            "__SUBMIT_URL__": _js_string(submit_url),
            "__STEP_COUNT__": str(total),
            "__STATUS_URL__": _js_string(status_url),
            "__GENERATION__": _js_string(generation),
            "__MERMAID_SRC__": _js_string(_MERMAID_SRC),
            "__MERMAID_INTEGRITY__": _js_string(_MERMAID_INTEGRITY),
            "__LIVE_MODE__": _js_string(live),
            "__LIVE_SCRIPT__": live_script,
            "__EXPORT_SCRIPT__": _export_script(),
            "__MERMAID__": _mermaid_script(_needs_mermaid(header_html, steps_html)),
        },
    )

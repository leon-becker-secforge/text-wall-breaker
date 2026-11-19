"""A small Markdown subset, rendered to HTML.

Deliberately not a CommonMark implementation. The prompt text an agent writes is short
and hand-authored, so the supported set is the one an agent actually reaches for:
headings, paragraphs, fenced code (syntax-colored when the fence names a language
Pygments knows) and inline code, unordered and ordered lists (nestable by
indentation), blockquotes, links, images, bold, italic, and strikethrough. Anything
outside that set renders as literal text rather than silently disappearing.

Two extensions beyond that, both closed-set so an unrecognized value still falls through
to literal text rather than rendering something unintended: `!badge[text](value)` for a
`base.css` severity badge (`value` is one of `success`/`warning`/`danger`/`info`), and a
` ```verdict ` fence for the one-sentence headline block `base.css` styles via
`[data-role="verdict"]` — its body is run through inline rendering like a paragraph,
not escaped as code, since it's prose.

A ` ```mermaid ` fence is the one exception to "no network fetch but the one POST" (see
page.py's module docstring): it's escaped and wrapped the same way any other fence is
here, but page.py loads a mermaid renderer from a CDN — not vendored — to draw it. See
`page.py`'s `_needs_mermaid`/`_MERMAID_SRC` for why and the tradeoff that came with it.

Raw HTML in the source is never passed through — every run of source text is escaped
before any tag this module emits is added, so a `<script>` in a question becomes visible
text. That is the whole reason rendering happens here rather than in the page's JS: the
page never sees a string it has to trust.

Line breaks inside a paragraph are preserved as `<br>`, which is GitHub's behaviour
rather than CommonMark's (where they collapse to a space). A prompt is written in a JSON
string with explicit newlines in it; honouring them is what the author meant.

A literal two-character `\n` (backslash then `n`, not a real newline byte) is treated the
same way, outside fenced blocks — see `_expand_literal_newlines`. An agent authoring a
tool-call argument has no interpreter turning `\n` into a control character first, so it
sometimes writes the escape where it means a line break; without this, that text would
print as a visible `\n` instead. The one gap this doesn't cover: a literal `\n` shown
deliberately inside an inline code span (as this docstring just did) is still expanded,
since the expansion runs before inline parsing knows where the code spans are.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from text_wall_breaker.highlight import highlight_code

__all__ = ["is_rendered_asset", "render_inline", "render_markdown"]

# Schemes a link or image may use. `file:` is here because the page is opened from disk
# and images are referenced as file:// absolute paths (that is the only image mechanism
# this server has; a bare absolute path is rewritten to one first, see `_image_url`).
# Everything else — `javascript:` above all — falls through to being rendered as plain
# text, so an unexpected scheme is visible instead of active.
_SAFE_SCHEMES = ("http://", "https://", "file://", "mailto:", "data:image/")

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
# Only a bare fence closes one: a ```python line inside a block is an example's opening.
_FENCE_CLOSE = re.compile(r"^```\s*$")
_BULLET = re.compile(r"^([ \t]*)[-*]\s+(.*)$")
_ORDERED = re.compile(r"^([ \t]*)\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")

# A thematic break on its own line: three or more of the same marker, nothing else. The
# three markers are the ones an agent actually reaches for; CommonMark's spaced variant
# (`- - -`) is not, so it is left out rather than guessed at.
_THEMATIC_BREAK = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")

# The two-character escape a literal `\n` is authored as, distinct from the real newline
# byte `split("\n")` below already breaks lines on.
_LITERAL_NEWLINE = re.compile(r"\\n")

# A GFM pipe-table separator cell: optional colons around one or more dashes, e.g. `---`,
# `:---`, `---:` or `:---:`. The row this belongs to is what tells a table apart from a
# paragraph that merely contains a `|` — no header row on its own is enough.
_TABLE_SEPARATOR_CELL = re.compile(r"^:?-+:?$")

# The closed severity set a badge's value may take — matches base.css's own rule that
# colour follows severity only; anything else falls through to literal text below.
_BADGE_VALUES = ("success", "warning", "danger", "info")

# A link/image URL: runs of non-space-non-paren characters, or a single parenthesised
# group of such characters — one level of nesting, enough for a URL like a Wikipedia
# disambiguation link (`.../a_(b)`) to keep its own parens without closing the markdown
# syntax early. `[^)\s]+` used to stop at the first `)`, truncating the href and leaking
# the rest of the URL as sibling text.
#
# Possessive (`++`, `*+`): the nested `(?:[^\s()]+|...)+` backtracked exponentially when
# the closing `)` never came, so a link like `[t](https://...` with 30 characters after
# the paren hung the server. A possessive run never gives characters back, and the groups
# cannot overlap, so the match is the same and a miss fails in linear time.
_URL_BODY = r"(?:[^\s()]++|\([^\s()]*+\))++"

# One alternation over the raw source: whatever does not match is escaped as literal
# text. Order matters — code spans come first so that `**` inside backticks stays
# literal, `!badge[...]` before the plain image pattern so `!` isn't consumed by it
# first, and `**` precedes `*` so bold is not mis-read as two italics.
#
# The `(?!`)` lookaheads around the tick group stop the backtick alternative from
# backtracking to a shorter run when no closing run of the same length exists: without
# them, three stray backticks in running prose (no fence, no matching close) pair their
# first two into a spurious empty `<code></code>` and leak the third as text, rather than
# falling through to literal text like every other unmatched construct here does.
_INLINE = re.compile(
    r"(?P<tick>`+)(?!`)(?P<code>.+?)(?P=tick)(?!`)"
    r"|!badge\[(?P<badge_text>[^\]]*)\]\((?P<badge_value>[A-Za-z]+)\)"
    rf"|!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>{_URL_BODY})\)"
    rf"|\[(?P<link_text>[^\]]*)\]\((?P<link_url>{_URL_BODY})\)"
    # The lookahead is what makes `**a *b***` close on the last pair rather than the
    # first: a non-greedy close would otherwise strand the trailing marker as text.
    # `*` and `**` need text against both markers, as CommonMark's flanking rule asks:
    # `2 * 3 * 4` is arithmetic, not emphasis.
    r"|\*\*(?P<b_text>\S(?:.*?\S)?)\*\*(?![*_])"
    # `_` only delimits at a word boundary, as in CommonMark: identifiers like set_mode
    # in running prose must not turn into emphasis. `*` still works intraword.
    r"|(?<!\w)__(?P<ub_text>.+?)__(?![*\w])"
    r"|\*(?P<i_text>[^\s*](?:.*?[^\s*])?)\*"
    r"|(?<!\w)_(?P<ui_text>.+?)_(?!\w)"
    r"|(?P<s>~~)(?P<s_text>.+?)(?P=s)",
    re.DOTALL,
)


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _safe_url(url: str) -> str | None:
    """Return `url` if it may be put in an href/src, else None.

    A URL with no scheme at all is rejected rather than treated as relative: the page
    lives in a throwaway temp directory, so a relative link could only ever resolve to
    something the agent did not mean to reference.
    """
    lowered = url.lower()
    if lowered.startswith(_SAFE_SCHEMES):
        return url
    return None


def _image_url(url: str) -> str:
    """`url` with a bare absolute or `~` path turned into the `file://` URL it means.

    Agents often write `![x](/tmp/shot.png)` instead of `![x](file:///tmp/shot.png)`.
    An image can only ever be a local file or a remote URL, so a leading `/` or `~/` is
    unambiguous here; a relative path stays rejected (see `_safe_url`).
    """
    if url.startswith("~/"):
        url = str(Path(url).expanduser())
    if url.startswith("/") and not url.startswith("//"):
        return f"file://{url}"
    return url


def render_inline(text: str, *, asset_base_url: str | None = None) -> str:
    """Render the inline span constructs in `text`, escaping everything else.

    `asset_base_url`, if set, is where a `file://` image is rewritten to instead of
    embedding the `file://` URL as-is — see `_render_match`'s image branch for why.
    """
    out: list[str] = []
    pos = 0
    for match in _INLINE.finditer(text):
        out.append(_escape(text[pos : match.start()]))
        out.append(_render_match(match, asset_base_url))
        pos = match.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def _image_src(url: str, asset_base_url: str | None) -> str:
    """The `src` to embed for image `url`, rewriting a `file://` one if asked to.

    A page opened straight from disk (`file://`) can load a `file://` image with no
    help; a page served over `http://` (a reused `ask`/`show` tab — see
    `listener.SessionListener`) cannot, since browsers refuse a `file://` subresource
    referenced from a non-`file://` document. `asset_base_url` is that session's
    `/asset?t=...` route (see `SessionListener.asset_base_url`/`read_asset`), which
    reads the file itself and streams it back over the same `http://` origin the page
    already trusts. `asset_base_url` is None exactly when there is no such session — the
    ordinary `file://`-opened page — where the raw URL already works.
    """
    if asset_base_url is not None and url.lower().startswith("file://"):
        path = unquote(urlsplit(url).path)
        _rendered_assets.add(path)
        return f"{asset_base_url}&path={quote(path, safe='')}"
    return url


# Every path `_image_src` put into a page as an `/asset` URL. `GET /asset` serves only
# these, so the token alone does not open every image-named file on disk.
_rendered_assets: set[str] = set()


def is_rendered_asset(path: str) -> bool:
    """Whether a page this process rendered references `path` through `/asset`."""
    return path in _rendered_assets


def _render_match(match: re.Match[str], asset_base_url: str | None) -> str:
    if match.group("tick"):
        # Code spans are escaped but not recursed into: backticks win over every other
        # inline marker, so `**x**` in code stays as typed.
        return f"<code>{_escape(match.group('code'))}</code>"

    if match.group("badge_value") is not None:
        value = match.group("badge_value").lower()
        if value not in _BADGE_VALUES:
            return _escape(match.group(0))
        text = render_inline(match.group("badge_text"), asset_base_url=asset_base_url)
        return f'<span class="badge" data-axis="severity" data-value="{value}">{text}</span>'

    if match.group("img_url") is not None:
        url = _safe_url(_image_url(match.group("img_url")))
        alt = _escape(match.group("img_alt"))
        # A remote image would tell its server the page was opened, and from where.
        if url is None or url.lower().startswith(("http://", "https://")):
            return _escape(match.group(0))
        src = _image_src(url, asset_base_url)
        return f'<img src="{_escape(src)}" alt="{alt}">'

    if match.group("link_url") is not None:
        url = _safe_url(match.group("link_url"))
        if url is None:
            return _escape(match.group(0))
        link_text = render_inline(match.group("link_text"), asset_base_url=asset_base_url)
        return f'<a href="{_escape(url)}">{link_text}</a>'

    bold = match.group("b_text") or match.group("ub_text")
    if bold is not None:
        return f"<strong>{render_inline(bold, asset_base_url=asset_base_url)}</strong>"

    if match.group("s"):
        s_text = render_inline(match.group("s_text"), asset_base_url=asset_base_url)
        return f"<del>{s_text}</del>"

    italic = match.group("i_text") or match.group("ui_text")
    return f"<em>{render_inline(italic, asset_base_url=asset_base_url)}</em>"


def _split_row(line: str) -> list[str]:
    """Split one `| a | b |` row into its cell texts, stripping the outer pipes."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _table_alignment(cell: str) -> str | None:
    left, right = cell.startswith(":"), cell.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return None


def _is_table_separator(line: str) -> bool:
    # A pipe is required, not just optional: a bare `---` is a thematic break (see
    # `_THEMATIC_BREAK`), and GFM itself never writes a separator row without pipes —
    # treating one as a single-cell table is what let a divider after a line that happens
    # to contain `|` swallow both lines into a bogus table.
    if "|" not in line:
        return False
    cells = _split_row(line)
    return bool(cells) and all(_TABLE_SEPARATOR_CELL.match(cell) for cell in cells)


def _render_table_row(
    tag: str, cells: list[str], aligns: list[str | None], asset_base_url: str | None
) -> str:
    rendered = []
    for index, cell in enumerate(cells):
        align = aligns[index] if index < len(aligns) else None
        style = f' style="text-align:{align}"' if align else ""
        cell_html = render_inline(cell, asset_base_url=asset_base_url)
        rendered.append(f"<{tag}{style}>{cell_html}</{tag}>")
    return "<tr>" + "".join(rendered) + "</tr>"


def _render_table(
    header: list[str],
    aligns: list[str | None],
    rows: list[list[str]],
    asset_base_url: str | None,
) -> str:
    thead = f"<thead>{_render_table_row('th', header, aligns, asset_base_url)}</thead>"
    body_rows = "".join(_render_table_row("td", row, aligns, asset_base_url) for row in rows)
    tbody = f"<tbody>{body_rows}</tbody>"
    # The wrapper scrolls a table wider than its column instead of widening the page.
    return f'<div class="table-wrap"><table>{thead}{tbody}</table></div>'


def _render_list(
    entries: list[tuple[int, str, str]], start: int, asset_base_url: str | None
) -> tuple[str, int]:
    """Render one `<ul>`/`<ol>` from `entries` starting at `start`, returning (html, next).

    `entries` is a flat (indent, tag, text) sequence for a contiguous run of list lines;
    nesting is inferred from indent increasing, the same way the block loop above infers
    nesting for nothing else — there is no separate tree built ahead of time. A deeper
    indent right after an item becomes that item's own nested list (recursing); a tag
    change at the *same* indent ends the current list rather than joining it, so `1. one`
    followed by `- two` renders as two sibling lists, not one with a mismatched marker.
    """
    indent, tag, _ = entries[start]
    items: list[str] = []
    index = start
    while index < len(entries) and entries[index][0] == indent and entries[index][1] == tag:
        content = render_inline(entries[index][2], asset_base_url=asset_base_url)
        index += 1
        if index < len(entries) and entries[index][0] > indent:
            nested_html, index = _render_list(entries, index, asset_base_url)
            content += nested_html
        items.append(f"<li>{content}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>", index


def _render_diff_block(body_lines: list[str]) -> str:
    """A ```diff fenced block, one `.line` per row, classified by its leading marker.

    The marker itself is stripped from the text and re-added by CSS (`::before`), which
    is what lets the minimal diff style show it in a fixed accent colour instead of
    whatever colour plain text happens to render in.
    """
    rows: list[str] = []
    for line in body_lines:
        if line.startswith("@@"):
            rows.append(f'<div class="line hunk">{_escape(line) or "&nbsp;"}</div>')
        elif line.startswith(("+++", "---")):
            rows.append(f'<div class="line file">{_escape(line) or "&nbsp;"}</div>')
        elif line[:1] in ("+", "-"):
            cls = "add" if line[0] == "+" else "del"
            rows.append(f'<div class="line {cls}">{_escape(line[1:]) or "&nbsp;"}</div>')
        else:
            text = line[1:] if line.startswith(" ") else line
            rows.append(f'<div class="line ctx">{_escape(text) or "&nbsp;"}</div>')
    return '<div class="pp-diff">' + "".join(rows) + "</div>"


def _expand_literal_newlines(lines: list[str]) -> list[str]:
    """Split a literal `\\n` escape into a real line break, everywhere except a fence.

    Skips fenced blocks (tracked the same way the main loop tracks them, via `_FENCE`)
    because a literal `\\n` there is often the point — regex source, JSON shown as text —
    and expanding it would corrupt code the author meant literally.
    """
    out: list[str] = []
    in_fence = False
    for line in lines:
        if (_FENCE_CLOSE if in_fence else _FENCE).match(line.strip()):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        out.extend(_LITERAL_NEWLINE.split(line))
    return out


# A question's first paragraph renders bold, so past this length it reads as a wall.
LONG_QUESTION_CHARS = 240
# Soft limit on the whole question: past it the page takes the wide column, so context
# in a long question is not squeezed into the narrow one. Never rejects a question.
WIDE_QUESTION_CHARS = 400
_MAX_LEAD_CHARS = 200


def first_paragraph(question: str) -> str:
    return question.strip().split("\n\n", 1)[0]


def _lead_end(paragraph: str, terminator: str) -> int:
    """Index just past the first `terminator` outside a code span or a markdown link, or -1.

    A `[link text](url)` is skipped as one unit, the same as a code span: a `. `/`: `
    inside the link text would otherwise get a `**` spliced into the middle of the
    `[...](...)` construct, which `_INLINE`'s link pattern then fails to match at all —
    turning a working link into a dangling `]` plus a raw, unlinked `(url)`.
    """
    in_code = False
    index = 0
    while index < len(paragraph):
        char = paragraph[index]
        if char == "`":
            in_code = not in_code
            index += 1
            continue
        if not in_code and char == "[":
            close = paragraph.find("]", index)
            if close != -1:
                index = close + 1
                if paragraph[index : index + 1] == "(":
                    paren_close = paragraph.find(")", index)
                    if paren_close != -1:
                        index = paren_close + 1
                continue
        if not in_code and char == terminator and paragraph[index + 1 : index + 2] in (" ", "\n"):
            return index + 1
        index += 1
    return -1


def bold_lead(question: str) -> str | None:
    """`question` with its first sentence in bold, or None unless its first paragraph is long.

    The first sentence ends at the first `. `, else the first `: `, outside code spans.
    One longer than `_MAX_LEAD_CHARS` is not bolded: it would be the same wall again.
    """
    paragraph = first_paragraph(question)
    if len(paragraph) <= LONG_QUESTION_CHARS:
        return None
    for terminator in (".", ":"):
        end = _lead_end(paragraph, terminator)
        if 0 < end <= _MAX_LEAD_CHARS:
            lead = paragraph[:end]
            # A lead that already carries its own `**`/`__` must not be wrapped in
            # another pair: the inline renderer pairs markers left to right, so nesting
            # them this way strands one marker as literal text and mis-closes the rest
            # (see markdown.py's test for the exact garbled output). Leaving the question
            # unbolded is better than corrupting it.
            if "**" in lead or "__" in lead:
                continue
            start = question.index(paragraph)
            return f"{question[:start]}**{lead}**{question[start + end :]}"
    return None


def render_markdown(text: str, *, asset_base_url: str | None = None) -> str:
    """Render `text` to an HTML fragment. Returns "" for empty or whitespace-only input.

    `asset_base_url` is threaded down to every inline render — see `_image_src`.
    """
    if not text or not text.strip():
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = _expand_literal_newlines(lines)
    out: list[str] = []
    paragraph: list[str] = []
    list_entries: list[tuple[int, str, str]] = []
    quote_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            rendered = (render_inline(line, asset_base_url=asset_base_url) for line in paragraph)
            out.append("<p>" + "<br>".join(rendered) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_entries:
            index = 0
            while index < len(list_entries):
                html, index = _render_list(list_entries, index, asset_base_url)
                out.append(html)
            list_entries.clear()

    def flush_quote() -> None:
        if quote_lines:
            rendered = (render_inline(line, asset_base_url=asset_base_url) for line in quote_lines)
            body = "<br>".join(rendered)
            out.append(f"<blockquote><p>{body}</p></blockquote>")
            quote_lines.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line.strip())

        if fence is not None:
            flush_all()
            language = fence.group(1)
            body: list[str] = []
            index += 1
            # An unterminated fence runs to the end of the input rather than erroring —
            # a truncated prompt should still render everything before the break.
            while index < len(lines) and not _FENCE_CLOSE.match(lines[index].strip()):
                body.append(lines[index])
                index += 1
            if language == "diff":
                out.append(_render_diff_block(body))
            elif language == "verdict":
                # Prose, not code: run through inline rendering same as a paragraph
                # would be, just wrapped for base.css's `[data-role="verdict"]` style.
                rendered = (render_inline(line, asset_base_url=asset_base_url) for line in body)
                inner = "<br>".join(rendered)
                out.append(f'<div data-role="verdict">{inner}</div>')
            elif language == "mermaid":
                # Rendered client-side by page.py's mermaid loader, which only appears on
                # a page that actually has one of these — see _needs_mermaid there. The
                # raw diagram source is escaped the same way any other fenced block's is;
                # mermaid.run() reads it back via .textContent, which un-escapes it.
                out.append(f'<pre class="mermaid">{_escape("\n".join(body))}</pre>')
            else:
                attr = f' data-language="{_escape(language)}"' if language else ""
                source = "\n".join(body)
                code = highlight_code(language, source) or _escape(source)
                out.append(f'<pre class="code-block"{attr}><code>{code}</code></pre>')
            index += 1
            continue

        if not line.strip():
            flush_all()
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading is not None:
            flush_all()
            level = len(heading.group(1))
            heading_html = render_inline(heading.group(2), asset_base_url=asset_base_url)
            out.append(f"<h{level}>{heading_html}</h{level}>")
            index += 1
            continue

        if _THEMATIC_BREAK.match(line.strip()):
            flush_all()
            out.append("<hr>")
            index += 1
            continue

        quote = _QUOTE.match(line)
        if quote is not None:
            flush_paragraph()
            flush_list()
            quote_lines.append(quote.group(1))
            index += 1
            continue

        # A table is a header row immediately followed by a separator row — checking the
        # next line is what tells it apart from a paragraph that just happens to contain
        # a `|`, since neither row is identifiable from itself alone. A line that is
        # itself a list item never qualifies as that header row, even with a `|` in its
        # text and a `| --- |`-shaped line right after it: list detection wins, or a
        # bullet like `- a | b` followed by a real separator would lose its `- ` marker
        # into a bogus table cell instead of staying a list.
        is_list_item = _BULLET.match(line) is not None or _ORDERED.match(line) is not None
        if (
            not is_list_item
            and "|" in line
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            flush_all()
            header = _split_row(line)
            aligns = [_table_alignment(cell) for cell in _split_row(lines[index + 1])]
            index += 2
            body_rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                body_rows.append(_split_row(lines[index]))
                index += 1
            out.append(_render_table(header, aligns, body_rows, asset_base_url))
            continue

        bullet = _BULLET.match(line)
        ordered = _ORDERED.match(line) if bullet is None else None
        item_match = bullet or ordered
        if item_match is not None:
            tag = "ul" if bullet is not None else "ol"
            flush_paragraph()
            flush_quote()
            indent = len(item_match.group(1).expandtabs())
            list_entries.append((indent, tag, item_match.group(2)))
            index += 1
            continue

        flush_list()
        flush_quote()
        paragraph.append(line)
        index += 1

    flush_all()
    return "".join(out)

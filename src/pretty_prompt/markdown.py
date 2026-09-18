"""A small Markdown subset, rendered to HTML.

Deliberately not a CommonMark implementation. The prompt text an agent writes is short
and hand-authored, so the supported set is the one an agent actually reaches for:
headings, paragraphs, fenced and inline code, unordered and ordered lists, links,
images, bold and italic. Anything outside that set renders as literal text rather than
silently disappearing.

Raw HTML in the source is never passed through — every run of source text is escaped
before any tag this module emits is added, so a `<script>` in a question becomes visible
text. That is the whole reason rendering happens here rather than in the page's JS: the
page never sees a string it has to trust.

Line breaks inside a paragraph are preserved as `<br>`, which is GitHub's behaviour
rather than CommonMark's (where they collapse to a space). A prompt is written in a JSON
string with explicit newlines in it; honouring them is what the author meant.
"""

from __future__ import annotations

import html
import re

__all__ = ["render_inline", "render_markdown"]

# Schemes a link or image may use. `file:` is here because the page is opened from disk
# and images are referenced as file:// absolute paths (that is the only image mechanism
# this server has). Everything else — `javascript:` above all — falls through to being
# rendered as plain text, so an unexpected scheme is visible instead of active.
_SAFE_SCHEMES = ("http://", "https://", "file://", "mailto:", "data:image/")

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^```\s*([A-Za-z0-9_+-]*)\s*$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_ORDERED = re.compile(r"^\d+[.)]\s+(.*)$")

# One alternation over the raw source: whatever does not match is escaped as literal
# text. Order matters — code spans come first so that `**` inside backticks stays
# literal, and `**` precedes `*` so bold is not mis-read as two italics.
_INLINE = re.compile(
    r"(?P<tick>`+)(?P<code>.+?)(?P=tick)"
    r"|!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>[^)\s]+)\)"
    r"|\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^)\s]+)\)"
    # The lookahead is what makes `**a *b***` close on the last pair rather than the
    # first: a non-greedy close would otherwise strand the trailing marker as text.
    r"|(?P<b>\*\*|__)(?P<b_text>.+?)(?P=b)(?![*_])"
    r"|(?P<i>[*_])(?P<i_text>.+?)(?P=i)",
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


def render_inline(text: str) -> str:
    """Render the inline span constructs in `text`, escaping everything else."""
    out: list[str] = []
    pos = 0
    for match in _INLINE.finditer(text):
        out.append(_escape(text[pos : match.start()]))
        out.append(_render_match(match))
        pos = match.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def _render_match(match: re.Match[str]) -> str:
    if match.group("tick"):
        # Code spans are escaped but not recursed into: backticks win over every other
        # inline marker, so `**x**` in code stays as typed.
        return f"<code>{_escape(match.group('code'))}</code>"

    if match.group("img_url") is not None:
        url = _safe_url(match.group("img_url"))
        alt = _escape(match.group("img_alt"))
        if url is None:
            return _escape(match.group(0))
        return f'<img src="{_escape(url)}" alt="{alt}">'

    if match.group("link_url") is not None:
        url = _safe_url(match.group("link_url"))
        if url is None:
            return _escape(match.group(0))
        return f'<a href="{_escape(url)}">{render_inline(match.group("link_text"))}</a>'

    if match.group("b"):
        return f"<strong>{render_inline(match.group('b_text'))}</strong>"

    return f"<em>{render_inline(match.group('i_text'))}</em>"


def render_markdown(text: str) -> str:
    """Render `text` to an HTML fragment. Returns "" for empty or whitespace-only input."""
    if not text or not text.strip():
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag = ""

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(render_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            rendered = "".join(f"<li>{render_inline(item)}</li>" for item in list_items)
            out.append(f"<{list_tag}>{rendered}</{list_tag}>")
            list_items.clear()
        list_tag = ""

    def flush_all() -> None:
        flush_paragraph()
        flush_list()

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
            while index < len(lines) and not _FENCE.match(lines[index].strip()):
                body.append(lines[index])
                index += 1
            attr = f' data-language="{_escape(language)}"' if language else ""
            code = _escape("\n".join(body))
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
            out.append(f"<h{level}>{render_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        bullet = _BULLET.match(line)
        ordered = _ORDERED.match(line) if bullet is None else None
        item_match = bullet or ordered
        if item_match is not None:
            tag = "ul" if bullet is not None else "ol"
            if list_tag and list_tag != tag:
                # A bullet directly after a numbered item starts a new list rather than
                # joining the old one, so the marker the author typed is the one shown.
                flush_list()
            flush_paragraph()
            list_tag = tag
            list_items.append(item_match.group(1))
            index += 1
            continue

        flush_list()
        paragraph.append(line)
        index += 1

    flush_all()
    return "".join(out)

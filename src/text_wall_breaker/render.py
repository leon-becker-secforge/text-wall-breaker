"""The rendered view of a Markdown or AsciiDoc file — CONTEXT.md's "Rendered view".

`rendered_view` turns the new side of a page into HTML in which every block carries
`data-first`/`data-last`, the new-side source lines it stands for. The page script uses
them to turn a click on a block into the ordinary range comment on those lines.

The text is untrusted file content shown in the operator's browser: raw HTML is escaped,
a remote image is replaced by its alt text (opening a review makes no network request),
and a local image is shown only if it resolves to a file inside the repository. A mermaid
fence is the one exception to the network rule: it becomes a `<pre class="mermaid">`, and
the page then loads the pinned renderer the way `markdown.py` describes. With the server
started with `--no-mermaid` it is an ordinary code block.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from .markdown import image_src, mermaid_enabled

__all__ = ["Block", "is_renderable", "rendered_view", "split_blocks"]

_MARKDOWN = {".md", ".markdown"}
_ASCIIDOC = {".adoc", ".asciidoc"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_LINK_SCHEMES = ("http://", "https://", "mailto:")

# Block tokens that open an element a comment may land on. A tight list item's paragraph is
# `hidden` (no tag is drawn for it), so the item carries the lines.
_BLOCKS = {
    "paragraph_open",
    "heading_open",
    "bullet_list_open",
    "ordered_list_open",
    "list_item_open",
    "blockquote_open",
    "table_open",
    "tr_open",
    "fence",
    "code_block",
    "hr",
}


def is_renderable(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _MARKDOWN | _ASCIIDOC


def rendered_view(
    path: str,
    lines: list[str],
    *,
    root: str | None = None,
    asset_base_url: str | None = None,
) -> str | None:
    """HTML of the file `lines` (the new side of `path`), or None if it cannot be shown."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _MARKDOWN:
        return _markdown(path, lines, root, asset_base_url)
    if suffix in _ASCIIDOC:
        return _asciidoc(path, lines, root, asset_base_url)
    return None


def _last_line(lines: list[str], first: int, end: int) -> int:
    """`end` (1-based, inclusive) moved up over trailing blank lines, never above `first`."""
    while end > first and not lines[end - 1].strip():
        end -= 1
    return end


def _local_image(src: str, path: str, root: str | None) -> str | None:
    """The `file://` URL of `src` if it is an image file inside the repository."""
    if root is None or "://" in src or src.startswith(("/", "~", "data:")):
        return None
    base = Path(root).resolve()
    candidate = (base / PurePosixPath(path).parent / src.split("#")[0].split("?")[0]).resolve()
    if candidate.suffix.lower() not in _IMAGE_SUFFIXES or not candidate.is_file():
        return None
    if base != candidate and base not in candidate.parents:
        return None
    return f"file://{candidate}"


def _tidy_inline(token: Token, path: str, root: str | None, asset_base_url: str | None) -> None:
    for child in token.children or []:
        if child.type == "image":
            src = _local_image(str(child.attrGet("src") or ""), path, root)
            if src is None:
                child.type, child.tag, child.attrs = "text", "", {}
                child.content = child.content or ""
            else:
                child.attrSet("src", image_src(src, asset_base_url))
        elif child.type == "link_open":
            href = str(child.attrGet("href") or "")
            if href.lower().startswith(_LINK_SCHEMES):
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
            else:
                child.attrs = {}


def _mermaid_fence(
    renderer: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """A ` ```mermaid ` fence as the `<pre class="mermaid">` the page script draws.

    Same element `markdown.py` writes for `ask` and `show`; the line marks stay on it so a
    click on the diagram still comments on the fence.
    """
    token = tokens[idx]
    if token.info.split(maxsplit=1)[:1] != ["mermaid"] or not mermaid_enabled():
        return renderer.fence(tokens, idx, options, env)
    token.attrJoin("class", "mermaid")
    return f"<pre{renderer.renderAttrs(token)}>{escape(token.content)}</pre>\n"


def _markdown(path: str, lines: list[str], root: str | None, asset_base_url: str | None) -> str:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.add_render_rule("fence", _mermaid_fence)
    tokens = parser.parse("\n".join(lines) + "\n")
    for token in tokens:
        if token.type in _BLOCKS and token.map:
            first = token.map[0] + 1
            token.attrSet("data-first", str(first))
            token.attrSet("data-last", str(_last_line(lines, first, token.map[1])))
        elif token.type == "inline":
            _tidy_inline(token, path, root, asset_base_url)
    return parser.renderer.render(tokens, parser.options, {})


# Read when the module loads, like page.py's templates: a running server must not pick up
# a script edited on disk afterwards that its own code does not match.
_ADOC_SCRIPT = Path(__file__).with_name("adoc_lines.rb").read_text(encoding="utf-8")
# A block attribute line (`[source,python]`, `[[id]]`) or title (`.Title`) above its block.
_BLOCK_PREFIX = re.compile(r"^(\[.*\]|\.[^\s.].*)\s*$")
_ADOC_TIMEOUT_SECONDS = 20

# What AsciiDoc output may contain once it is on the page; anything else is dropped, with
# the content of a script or style element.
_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col", "colgroup", "dd",
    "del", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q", "s", "section", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "tt", "u", "ul",
}  # fmt: skip
# No `id`: it could shadow an element the page script looks up by id, and no in-page link
# survives cleaning to use one.
_ATTRS = {"title", "colspan", "rowspan", "start", "type", "alt", "data-first"}
# Class prefixes the page's own styles and script select on; a file must not borrow them.
_PAGE_CLASS_PREFIXES = ("twb-", "is-", "has-", "deck-")


def _file_classes(value: str) -> str:
    return " ".join(c for c in value.split() if not c.startswith(_PAGE_CLASS_PREFIXES))


_VOID = {"br", "col", "hr", "img"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template"}


class _Cleaner(HTMLParser):
    """Copies allowed markup, and gives each `data-first` element its `data-last`.

    An element ends where the next marked element that is not inside it begins, less
    trailing blank lines: AsciiDoc reports where a block starts, not where it ends.
    """

    def __init__(
        self, lines: list[str], path: str, root: str | None, asset_base_url: str | None
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._lines, self._path, self._root, self._asset = lines, path, root, asset_base_url
        self.out: list[str] = []
        self._stack: list[tuple[str, int | None]] = []  # (tag, index into marks)
        self._marks: list[tuple[int, int]] = []  # (first line, index into out)
        self._ends: dict[int, int] = {}  # mark -> marks started when it closed
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `_skip` counts open dropped elements only: their end tags are the only ones that
        # bring it back down. `embed` has no end tag, so it drops just itself.
        if tag in _DROP_WITH_CONTENT and tag != "embed":
            self._skip += 1
        if self._skip or tag in _DROP_WITH_CONTENT:
            return
        if tag not in _TAGS:
            return
        kept: list[str] = []
        mark: int | None = None
        for name, raw in attrs:
            value = raw or ""
            if name == "data-first" and value.isdigit():
                mark = len(self._marks)
                self._marks.append((int(value), len(self.out)))
                kept.append(f'data-first="{int(value)}"')
            elif name == "class" and (classes := _file_classes(value)):
                kept.append(f'class="{escape(classes, quote=True)}"')
            elif name in _ATTRS and name != "data-first":
                kept.append(f'{name}="{escape(value, quote=True)}"')
            elif name == "href" and value.lower().startswith(_LINK_SCHEMES):
                kept.append(f'href="{escape(value, quote=True)}" target="_blank"')
                kept.append('rel="noopener noreferrer"')
            elif name == "src" and tag == "img":
                src = _local_image(value, self._path, self._root)
                if src is not None:
                    kept.append(f'src="{escape(image_src(src, self._asset), quote=True)}"')
        if tag == "img" and not any(a.startswith("src=") for a in kept):
            alt = dict(attrs).get("alt") or ""
            self.out.append(escape(alt))  # a remote or outside image shows as its alt text
            return
        self.out.append(f"<{tag}{' ' if kept else ''}{' '.join(kept)}>")
        if tag not in _VOID:
            self._stack.append((tag, mark))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            return  # `<object/>`: nothing inside to drop, and no end tag will come
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            # `embed` never opened a skip, so its stray end tag must not close one.
            if tag in _DROP_WITH_CONTENT and tag != "embed":
                self._skip -= 1
            return
        if tag not in _TAGS or tag in _VOID:
            return
        while self._stack:
            open_tag, mark = self._stack.pop()
            if mark is not None:
                self._ends[mark] = len(self._marks)
            if open_tag == tag:
                break
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(escape(data))

    def _start(self, first: int) -> int:
        """`first` moved up over the attribute and title lines that belong to its block."""
        while first > 1 and _BLOCK_PREFIX.match(self._lines[first - 2]):
            first -= 1
        return first

    def result(self) -> str:
        starts = [self._start(first) for first, _ in self._marks]
        for mark, (first, index) in enumerate(self._marks):
            following = self._ends.get(mark, len(self._marks))
            end = starts[following] - 1 if following < len(self._marks) else len(self._lines)
            first_now = starts[mark]
            last = _last_line(self._lines, first_now, max(first_now, min(end, len(self._lines))))
            self.out[index] = self.out[index].replace(
                f'data-first="{first}"', f'data-first="{first_now}" data-last="{last}"', 1
            )
        return "".join(self.out)


def _asciidoc(
    path: str, lines: list[str], root: str | None, asset_base_url: str | None
) -> str | None:
    """HTML of an AsciiDoc file via Asciidoctor, or None when `ruby` or the gem is missing."""
    ruby = shutil.which("ruby")
    if ruby is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603
            # The word after the script is the script's ARGV: it leaves `[mermaid]` a listing.
            [ruby, "-e", _ADOC_SCRIPT, *([] if mermaid_enabled() else ["no-mermaid"])],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_ADOC_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    cleaner = _Cleaner(lines, path, root, asset_base_url)
    cleaner.feed(done.stdout)
    cleaner.close()
    return cleaner.result()


class Block(NamedTuple):
    """One outermost marked element of a rendered view, and the lines it stands for."""

    first: int
    last: int
    html: str


class _Splitter(HTMLParser):
    """Cuts `rendered_view` output into its outermost `data-first` elements.

    Wrapper elements without the mark (AsciiDoc sections) are looked through, and dropped
    with whatever sits between the blocks: the output is already sanitised, so what is
    copied here is exactly what `rendered_view` wrote. A `<pre>` is the exception: a fence
    carries its mark on the `<code>` inside, so the `<pre>` opens the block and its range
    is that of the first marked element within.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[Block] = []
        self._parts: list[str] = []
        self._depth = 0
        self._range: tuple[int, int] | None = None

    @staticmethod
    def _lines(attrs: list[tuple[str, str | None]]) -> tuple[int, int] | None:
        marks = dict(attrs)
        first, last = marks.get("data-first"), marks.get("data-last")
        if not (first and first.isdigit()):
            return None
        return int(first), int(last) if last and last.isdigit() else int(first)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or ""
        marked = self._lines(attrs)
        if self._depth == 0:
            if marked is None and tag != "pre":
                return
            self._range = marked
        elif self._range is None:
            self._range = marked
        if tag in _VOID:
            if self._depth:
                self._parts.append(raw)
            else:
                self._close([raw])
            return
        self._depth += 1
        self._parts.append(raw)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self._depth or tag in _VOID:
            return
        self._parts.append(f"</{tag}>")
        self._depth -= 1
        if self._depth == 0:
            self._close(self._parts)

    def _close(self, parts: list[str]) -> None:
        if self._range is not None:
            self.blocks.append(Block(self._range[0], self._range[1], "".join(parts)))
        self._parts = []
        self._range = None

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._depth:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._depth:
            self._parts.append(f"&#{name};")


def split_blocks(rendered: str) -> list[Block]:
    """The outermost marked blocks of `rendered_view` output, in document order."""
    splitter = _Splitter()
    splitter.feed(rendered)
    splitter.close()
    return sorted(splitter.blocks, key=lambda block: block.first)

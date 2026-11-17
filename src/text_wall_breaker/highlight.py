"""Syntax coloring for the rows of a review diff and for fenced code blocks.

Both come back as HTML whose token spans carry `tk-*` classes (Pygments short names behind
a prefix); the palette lives in `assets/tokens.css`.
"""

from __future__ import annotations

import html

from pygments import format as pygments_format
from pygments import lex
from pygments.formatters.html import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename
from pygments.util import ClassNotFound

from text_wall_breaker.diff import DiffLine

# Above this a side is shown plain: lexing a generated or minified file costs more than
# the coloring is worth.
_MAX_CHARS = 200_000

_FORMATTER = HtmlFormatter(nowrap=True, classprefix="tk-")


def _lexer_for(path: str) -> Lexer | None:
    try:
        return get_lexer_for_filename(path, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


def _color_lines(lexer: Lexer, lines: list[str]) -> list[str]:
    """Each line as HTML, lexed together so a string or comment may span lines."""
    source = "\n".join(lines)
    if len(source) > _MAX_CHARS:
        return [html.escape(line) for line in lines]
    pieces = pygments_format(lex(source, lexer), _FORMATTER).split("\n")
    # Some lexers end the stream with a newline of their own even with ensurenl=False.
    if len(pieces) == len(lines) + 1 and pieces[-1] == "":
        pieces.pop()
    # Pygments keeps one output line per input line; if a lexer ever disagrees, plain
    # text is safer than rows attached to the wrong source line.
    if len(pieces) != len(lines):
        return [html.escape(line) for line in lines]
    return pieces


def highlight_code(language: str, source: str) -> str | None:
    """`source` as token-span HTML for a fence tagged `language`, or None without a lexer."""
    if not language:
        return None
    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None
    return "\n".join(_color_lines(lexer, source.split("\n")))


def highlight_rows(path: str, rows: list[DiffLine]) -> dict[int, str]:
    """HTML for each non-hunk row of `rows`, keyed by its index in `rows`.

    The old and the new side of each hunk are lexed separately, as the code reads on that
    side: a deleted line next to its replacement would otherwise confuse the lexer. A hunk
    starts mid-file, so state from before it (an open docstring) is unknown and the first
    lines may color wrongly. Empty when the file's type has no lexer.
    """
    lexer = _lexer_for(path)
    if lexer is None:
        return {}
    out: dict[int, str] = {}
    hunk: list[tuple[int, DiffLine]] = []

    def flush() -> None:
        for sides in (("del", "ctx"), ("add", "ctx")):
            members = [(i, row) for i, row in hunk if row.kind in sides]
            if not members:
                continue
            colored = _color_lines(lexer, [row.text for _, row in members])
            for (i, row), piece in zip(members, colored, strict=True):
                # A context row is on both sides; the new side wins.
                if row.kind != "ctx" or sides[0] == "add":
                    out[i] = piece
        hunk.clear()

    for index, row in enumerate(rows):
        if row.kind == "hunk":
            flush()
        else:
            hunk.append((index, row))
    flush()
    return out

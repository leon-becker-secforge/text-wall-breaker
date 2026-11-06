"""Marks the characters that changed inside a deleted line and the line that replaced it.

A review row already shows whole lines as added or deleted. When a deleted line is followed
by a replacement that looks like it, the words that differ are wrapped in
`<mark class="pp-chg">`, so a one-word edit in a long line is found at a glance.
"""

from __future__ import annotations

import html
import re
from difflib import SequenceMatcher

from pretty_prompt.diff import DiffLine
from pretty_prompt.highlight import highlight_rows

__all__ = ["changed_ranges", "decorated_rows", "mark_ranges"]

# Two lines count as the same line edited when at least this share of their tokens match.
# Below it the lines are unrelated and marking most of both would only add noise.
_MIN_SIMILARITY = 0.5

# Longer lines are left alone: the token diff is quadratic in the worst case.
_MAX_CHARS = 2000

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
_HTML_PIECE = re.compile(r"<[^>]*>|&(?:#x?[0-9a-fA-F]+|\w+);|.", re.DOTALL)

Range = tuple[int, int]


def changed_ranges(old: str, new: str) -> tuple[list[Range], list[Range]] | None:
    """Character ranges of `old` and `new` that differ, or None if they are not alike.

    Tokens are words, runs of whitespace and single symbols, so a changed word is marked
    whole rather than as the letters that happen to differ. Changes separated only by
    whitespace merge into one range.
    """
    if old == new or len(old) > _MAX_CHARS or len(new) > _MAX_CHARS:
        return None
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() < _MIN_SIMILARITY:
        return None
    a_at = _offsets(a)
    b_at = _offsets(b)
    old_ranges: list[Range] = []
    new_ranges: list[Range] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            old_ranges.append((a_at[i1], a_at[i2]))
        if j2 > j1:
            new_ranges.append((b_at[j1], b_at[j2]))
    return _merge(old_ranges, old), _merge(new_ranges, new)


def _offsets(tokens: list[str]) -> list[int]:
    out = [0]
    for token in tokens:
        out.append(out[-1] + len(token))
    return out


def _merge(ranges: list[Range], text: str) -> list[Range]:
    merged: list[Range] = []
    for start, end in ranges:
        if merged and not text[merged[-1][1] : start].strip():
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def mark_ranges(row_html: str, ranges: list[Range]) -> str:
    """`row_html` (escaped text, maybe with token spans) with `ranges` of its text marked.

    Offsets count characters of the text, an entity as one. A mark never spans a tag: it is
    closed before one and reopened after, so the token spans stay well nested.
    """
    if not ranges:
        return row_html
    out: list[str] = []
    position = 0
    open_mark = False
    for piece in _HTML_PIECE.findall(row_html):
        if piece.startswith("<"):
            if open_mark:
                out.append("</mark>")
                open_mark = False
            out.append(piece)
            continue
        inside = any(start <= position < end for start, end in ranges)
        if inside and not open_mark:
            out.append('<mark class="pp-chg">')
            open_mark = True
        elif not inside and open_mark:
            out.append("</mark>")
            open_mark = False
        out.append(piece)
        position += 1
    if open_mark:
        out.append("</mark>")
    return "".join(out)


def decorated_rows(path: str, rows: list[DiffLine]) -> dict[int, str]:
    """HTML for the rows that have something to show beyond their plain text.

    Syntax-colored rows (see `highlight.highlight_rows`) with the changed characters of
    each edited line marked. A row with neither is absent; callers fall back to its
    escaped text.
    """
    out = dict(highlight_rows(path, rows))
    i = 0
    while i < len(rows):
        if rows[i].kind != "del":
            i += 1
            continue
        dels = []
        while i < len(rows) and rows[i].kind == "del":
            dels.append(i)
            i += 1
        adds = []
        while i < len(rows) and rows[i].kind == "add":
            adds.append(i)
            i += 1
        for old_index, new_index in zip(dels, adds, strict=False):
            found = changed_ranges(rows[old_index].text, rows[new_index].text)
            if found is None:
                continue
            for index, ranges in ((old_index, found[0]), (new_index, found[1])):
                base = out.get(index) or html.escape(rows[index].text)
                out[index] = mark_ranges(base, ranges)
    return out

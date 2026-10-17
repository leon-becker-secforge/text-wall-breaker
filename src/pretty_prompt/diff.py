"""Parses a unified diff into per-line old/new coordinates for `review`.

Only what `review` needs: enough structure to render each line with a clickable,
resolvable address — not a general-purpose diff/patch library. Deliberately tolerant of
missing `---`/`+++` file-header lines, since the caller already gives the path via
`review.Page.path`; a diff that starts straight at its first `@@` hunk is accepted as-is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["DiffLine", "InvalidDiff", "parse_unified_diff"]

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class InvalidDiff(ValueError):
    """`diff` text has no hunk pretty-prompt can resolve a line coordinate from."""


@dataclass(frozen=True)
class DiffLine:
    """One rendered row of a page's diff.

    `kind` is "hunk" (the `@@ ...` header itself, not addressable), "add", "del", or
    "ctx". `old_line`/`new_line` are the line's 1-based number in that side of the file,
    or None where that side doesn't have this line (an "add" has no `old_line`, a "del"
    no `new_line"). A context line carries both.
    """

    kind: str
    text: str
    old_line: int | None
    new_line: int | None

    @property
    def side(self) -> str | None:
        """Which side a comment on this row addresses, or None for a "hunk" row.

        `new_line` wins when both exist (a context line) — see CONTEXT.md's "Comment":
        the coordinate `review` hands back is the one the agent can act on in the file
        as it now stands.
        """
        if self.kind == "hunk":
            return None
        return "new" if self.new_line is not None else "old"

    @property
    def line(self) -> int | None:
        """The line number `side` refers to, or None for a "hunk" row."""
        if self.kind == "hunk":
            return None
        return self.new_line if self.new_line is not None else self.old_line


def parse_unified_diff(diff: str) -> list[DiffLine]:
    """Parse `diff` into its renderable, addressable lines, in order.

    Raises `InvalidDiff` if it contains no `@@ -a,b +c,d @@` hunk header — nothing to
    resolve a line's coordinate against, and rendering it as a diff at all would
    misrepresent it.
    """
    lines = diff.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    rows: list[DiffLine] = []
    old_no = 0
    new_no = 0
    in_hunk = False
    for raw in lines:
        header = _HUNK_HEADER.match(raw)
        if header is not None:
            old_no = int(header.group(1))
            new_no = int(header.group(2))
            in_hunk = True
            rows.append(DiffLine(kind="hunk", text=raw, old_line=None, new_line=None))
            continue
        if not in_hunk:
            # Before the first hunk: `--- a/path`/`+++ b/path` file headers, or nothing
            # (a caller who starts straight at `@@`). Either way, nothing addressable
            # yet, and the path itself is already given by Page.path — skip.
            continue
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file" — not a real row
        if raw.startswith("+"):
            rows.append(DiffLine(kind="add", text=raw[1:], old_line=None, new_line=new_no))
            new_no += 1
        elif raw.startswith("-"):
            rows.append(DiffLine(kind="del", text=raw[1:], old_line=old_no, new_line=None))
            old_no += 1
        else:
            text = raw[1:] if raw.startswith(" ") else raw
            rows.append(DiffLine(kind="ctx", text=text, old_line=old_no, new_line=new_no))
            old_no += 1
            new_no += 1

    if not any(row.kind == "hunk" for row in rows):
        raise InvalidDiff("no @@ hunk header found; is this a unified diff?")
    return rows

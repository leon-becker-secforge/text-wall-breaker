"""Parses a unified diff into per-line old/new coordinates for `review`.

Only what `review` needs: enough structure to render each line with a clickable,
resolvable address — not a general-purpose diff/patch library. Deliberately tolerant of
missing `---`/`+++` file-header lines, since the caller already gives the path via
`review.Page.path`; a diff that starts straight at its first `@@` hunk is accepted as-is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "DiffLine",
    "Gap",
    "InvalidDiff",
    "expansion_lines",
    "file_status",
    "gaps_of",
    "parse_unified_diff",
    "rows_with_context",
    "split_lines",
]

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_HUNK_COUNTS = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


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


_NEW_FILE_HUNK = re.compile(r"^@@ -0(?:,0)? \+")
_DELETED_FILE_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+0(?:,0)? @@")


def file_status(diff: str) -> tuple[str, str | None]:
    """How the file changed: `("A" | "M" | "D" | "R", old path of a rename or None)`.

    A diff with git's `diff --git` header is read from its extended header lines. A bare
    diff (what an agent writes by hand) is read from its `---`/`+++` lines, else from its
    first hunk: a file that starts at line 0 is new, one that ends at line 0 is deleted.
    """
    lines = diff.replace("\r\n", "\n").split("\n")
    in_git_header = bool(lines) and lines[0].startswith("diff --git ")
    old_path: str | None = None
    status = "M"
    seen_old = seen_new = False
    for raw in lines:
        if raw.startswith("@@"):
            if in_git_header or seen_old or seen_new:
                break
            if _NEW_FILE_HUNK.match(raw):
                status = "A"
            elif _DELETED_FILE_HUNK.match(raw):
                status = "D"
            break
        if raw.startswith("new file mode"):
            return "A", None
        if raw.startswith("deleted file mode"):
            return "D", None
        if raw.startswith("rename from "):
            old_path = raw[len("rename from ") :]
        elif raw.startswith("rename to ") and old_path is not None:
            return "R", old_path
        elif raw.startswith("--- "):
            seen_old = True
            if raw[4:].strip() == "/dev/null":
                status = "A"
        elif raw.startswith("+++ "):
            seen_new = True
            if raw[4:].strip() == "/dev/null":
                status = "D"
    return status, None


@dataclass(frozen=True)
class Gap:
    """A stretch of unchanged lines a diff does not show.

    `new_first`..`new_last` are its 1-based line numbers in the new file; an old line
    number is the new one plus `delta`. `position` is "top" (before the first hunk),
    "between" or "end" (after the last); `hunk` is the index of the hunk it precedes, or
    the number of hunks for "end".
    """

    new_first: int
    new_last: int
    delta: int
    position: str
    hunk: int

    @property
    def size(self) -> int:
        return self.new_last - self.new_first + 1


def split_lines(text: str) -> list[str]:
    """The lines of a file as the diff shows them: split on `\\n` only, no line ending."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _hunk_spans(diff: str) -> list[tuple[int, int, int, int]]:
    """Per hunk, (old_first, old_next, new_first, new_next) in diff order.

    `*_first` is the first line the hunk covers and `*_next` the line after its last. A
    side with a zero count (a pure addition or deletion) covers nothing; its start then
    names the line *before* the change, so its first line is the one after.
    """
    spans = []
    for raw in diff.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _HUNK_COUNTS.match(raw)
        if match is None:
            continue
        old_start, new_start = int(match.group(1)), int(match.group(3))
        old_count = 1 if match.group(2) is None else int(match.group(2))
        new_count = 1 if match.group(4) is None else int(match.group(4))
        old_first = old_start if old_count else old_start + 1
        new_first = new_start if new_count else new_start + 1
        spans.append((old_first, old_first + old_count, new_first, new_first + new_count))
    return spans


def gaps_of(diff: str, line_count: int) -> list[Gap]:
    """The unchanged stretches `diff` leaves out of a new file of `line_count` lines.

    Empty if the hunks do not fit that file, so a file that changed since the diff was
    taken is never expanded.
    """
    spans = _hunk_spans(diff)
    if not spans or spans[-1][3] - 1 > line_count:
        return []
    gaps: list[Gap] = []
    old_first, _, new_first, _ = spans[0]
    if new_first > 1:
        gaps.append(Gap(1, new_first - 1, old_first - new_first, "top", 0))
    for index in range(len(spans) - 1):
        _, old_next, _, new_next = spans[index]
        new_first_after = spans[index + 1][2]
        if new_first_after > new_next:
            gaps.append(
                Gap(new_next, new_first_after - 1, old_next - new_next, "between", index + 1)
            )
    _, old_next, _, new_next = spans[-1]
    if new_next <= line_count:
        gaps.append(Gap(new_next, line_count, old_next - new_next, "end", len(spans)))
    return gaps


def shown_new_side(diff: str) -> list[str]:
    """The new side as far as `diff` shows it, each line at its number.

    For a page whose file text is unknown: the lines between the hunks are blank, so a
    rendered view built from this shows the hunks' text and nothing invented. Never
    used for expansion, since a blank here is not the file's line.
    """
    shown = {row.new_line: row.text for row in parse_unified_diff(diff) if row.new_line}
    if not shown:
        return []
    return [shown.get(number, "") for number in range(1, max(shown) + 1)]


def expansion_lines(diff: str, text: str) -> list[str] | None:
    """`text` as lines if it is the new side `diff` was made from, else None.

    Every new-side row of the diff must equal the file's line at its number, which is
    what makes the hidden lines between the hunks safe to show and to number. A diff
    with no new-side row (a deleted file) proves nothing, so it is never expanded.
    """
    lines = split_lines(text)
    checked = 0
    for row in parse_unified_diff(diff):
        if row.kind == "hunk" or row.new_line is None:
            continue
        if row.new_line > len(lines) or lines[row.new_line - 1] != row.text:
            return None
        checked += 1
    return lines if checked else None


def rows_with_context(diff: str, lines: list[str]) -> list[DiffLine]:
    """The addressable rows of `diff` with every hidden stretch filled in from `lines`.

    In display order, which is what a comment's range is measured in. The hunk header
    rows are left out, as they are not addressable.
    """
    gaps = gaps_of(diff, len(lines))
    before: dict[int, Gap] = {gap.hunk: gap for gap in gaps if gap.position != "end"}

    def gap_rows(gap: Gap) -> list[DiffLine]:
        return [
            DiffLine("ctx", lines[number - 1], number + gap.delta, number)
            for number in range(gap.new_first, gap.new_last + 1)
        ]

    rows: list[DiffLine] = []
    hunk = -1
    for row in parse_unified_diff(diff):
        if row.kind == "hunk":
            hunk += 1
            if hunk in before:
                rows.extend(gap_rows(before[hunk]))
            continue
        rows.append(row)
    rows.extend(row for gap in gaps if gap.position == "end" for row in gap_rows(gap))
    return rows

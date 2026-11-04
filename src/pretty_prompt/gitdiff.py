"""Builds `review` pages from a git range, so the agent need not paste a diff back.

Runs `git diff` itself and splits the result per file, exactly the unit `review.Page`
paginates by. Files without a hunk (binary, pure mode change) cannot carry a line
comment and are skipped.

Each page also carries the file's new-side text when it can be trusted, so the page can
show the lines between the hunks: from the working tree when the diff is against it, from
the end revision of a range otherwise. A file over MAX_FILE_BYTES, one past the total
budget, a binary or non-UTF-8 file, a symlink, and a file that does not match the diff
are simply not expandable.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .diff import expansion_lines

__all__ = ["MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "GitDiffError", "pages_from_git"]

_GIT_TIMEOUT_SECONDS = 30
_FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_NO_FILE = "/dev/null"

# What may travel inside the page for expanding context: per file, and over all files of
# one call. Past either, a file shows only its hunks.
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024


class GitDiffError(ValueError):
    """The git source cannot be turned into pages."""


def _strip_prefix(raw: str) -> str:
    # `git diff` appends a tab after paths containing spaces.
    name = raw.rstrip("\t").strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    return name[2:] if name[:2] in ("a/", "b/") else name


def _path_of(chunk: str) -> str | None:
    old = new = None
    for line in chunk.split("\n"):
        if line.startswith("@@"):
            break
        if line.startswith("--- "):
            old = _strip_prefix(line[4:])
        elif line.startswith("+++ "):
            new = _strip_prefix(line[4:])
    path = new if new and new != _NO_FILE else old
    return path if path and path != _NO_FILE else None


def _end_revision(revision: str) -> str | None:
    """The revision a diff's new side is, or None when it is the working tree.

    `A..B` and `A...B` end at B (an empty B means HEAD), `X^!` at X; a single revision is
    compared with the working tree.
    """
    if "..." in revision:
        end = revision.split("...", 1)[1]
    elif ".." in revision:
        end = revision.split("..", 1)[1]
    elif revision.endswith("^!"):
        end = revision[:-2]
    else:
        return None
    return end or "HEAD"


def _read_new_side(directory: Path, revision: str, path: str) -> bytes | None:
    """The file's bytes on the diff's new side, or None if it cannot be read safely."""
    end = _end_revision(revision)
    if end is None:
        file = directory / path
        try:
            # A symlink would read whatever it points at, which may be outside the repo.
            if file.is_symlink() or not file.resolve().is_relative_to(directory.resolve()):
                return None
            if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
                return None
            return file.read_bytes()
        except OSError:
            return None
    if end.startswith("-"):
        return None
    command = ["git", "-C", str(directory), "show", f"{end}:{path}"]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell; end cannot start with "-"
            command, capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _expandable_text(directory: Path, revision: str, path: str, diff: str) -> str | None:
    """The new side's text if it is small, text, and exactly what `diff` was made from."""
    data = _read_new_side(directory, revision, path)
    if data is None or len(data) > MAX_FILE_BYTES or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if expansion_lines(diff, text) is not None else None


def pages_from_git(
    repo: str, git_range: str | None, paths: list[str] | None, context_lines: int
) -> list[dict[str, str]]:
    """One `{"path", "diff"}` per changed file of `git diff <git_range>` in `repo`.

    `git_range` defaults to `HEAD` (tracked changes in the working tree). Renames are
    shown as a delete plus an add. A page whose new-side file can be shown in full also
    has `"text"`: that file, for the page to expand the lines between the hunks.
    """
    directory = Path(repo).expanduser()
    if not directory.is_dir():
        raise GitDiffError(f"repo is not a directory: {repo}")
    if context_lines < 0:
        raise GitDiffError("context_lines must not be negative")
    revision = (git_range or "HEAD").strip()
    # A leading dash would be read by git as an option, not a revision.
    if not revision or revision.startswith("-"):
        raise GitDiffError(f"invalid git_range: {git_range!r}")

    command = [
        "git",
        "-C",
        str(directory),
        "-c",
        "core.quotepath=off",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        f"-U{context_lines}",
        revision,
        "--",
        *(paths or []),
    ]
    try:
        # A file that is not UTF-8 still diffs; its bytes show as U+FFFD instead of
        # failing the whole call.
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell; range cannot start with "-"
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitDiffError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitDiffError(f"git diff took over {_GIT_TIMEOUT_SECONDS}s") from exc
    if completed.returncode != 0:
        raise GitDiffError(f"git diff failed: {completed.stderr.strip()}")

    starts = [match.start() for match in _FILE_HEADER.finditer(completed.stdout)]
    ends = [*starts[1:], len(completed.stdout)][: len(starts)]
    chunks = [completed.stdout[start:end] for start, end in zip(starts, ends, strict=True)]
    pages: list[dict[str, str]] = []
    budget = MAX_TOTAL_BYTES
    for chunk in chunks:
        path = _path_of(chunk)
        if path is not None and "\n@@ " in chunk:
            page = {"path": path, "diff": chunk}
            text = _expandable_text(directory, revision, path, chunk)
            if text is not None and len(text.encode("utf-8")) <= budget:
                budget -= len(text.encode("utf-8"))
                page["text"] = text
            pages.append(page)
    if not pages:
        raise GitDiffError(f"no text changes in `git diff {revision}`")
    return pages

"""Builds `review` pages from a git range, so the agent need not paste a diff back.

Runs `git diff` itself and splits the result per file, exactly the unit `review.Page`
paginates by. Files without a hunk (binary, pure mode change) cannot carry a line
comment and are skipped.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

__all__ = ["GitDiffError", "pages_from_git"]

_GIT_TIMEOUT_SECONDS = 30
_FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_NO_FILE = "/dev/null"


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


def pages_from_git(
    repo: str, git_range: str | None, paths: list[str] | None, context_lines: int
) -> list[dict[str, str]]:
    """One `{"path", "diff"}` per changed file of `git diff <git_range>` in `repo`.

    `git_range` defaults to `HEAD` (tracked changes in the working tree). Renames are
    shown as a delete plus an add.
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
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell; range cannot start with "-"
            command, capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS, check=False
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
    for chunk in chunks:
        path = _path_of(chunk)
        if path is not None and "\n@@ " in chunk:
            pages.append({"path": path, "diff": chunk})
    if not pages:
        raise GitDiffError(f"no text changes in `git diff {revision}`")
    return pages

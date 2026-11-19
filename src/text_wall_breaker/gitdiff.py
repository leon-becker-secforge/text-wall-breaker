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

import os
import re
import subprocess
from pathlib import Path

from .diff import expansion_lines

__all__ = ["MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "GitDiffError", "pages_from_git", "repo_root"]

_GIT_TIMEOUT_SECONDS = 30
_FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_NO_FILE = "/dev/null"
# Added to a rename without edits: a page needs a hunk header, and this one tells the
# reader why there is nothing else to see.
_RENAME_ONLY_HUNK = "@@ -0,0 +0,0 @@ renamed without changes\n"

# What may travel inside the page for expanding context: per file, and over all files of
# one call. Past either, a file shows only its hunks.
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024

# The repo may be untrusted: its config can name commands git runs while it diffs. Each
# way in is shut separately, since git has no "run nothing from this repo" switch:
# - core.fsmonitor: off.
# - filter drivers, selected by .gitattributes, $GIT_DIR/info/attributes or
#   core.attributesFile: in-tree attributes come from the empty tree (--attr-source,
#   git 2.40), the attributes file is /dev/null, and every filter driver the config
#   names is blanked (`_filter_blanks`), which also covers info/attributes.
# - diff drivers (textconv, external diff): --no-textconv --no-ext-diff on `git diff`.
# - submodules, diffed by a child git under their own config: --ignore-submodules=all.
# - a missing object of a partial clone, fetched through the repo's own remote URL
#   (`ext::<command>`): no transport is allowed, and lazy fetching is off (git 2.44).
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_NO_REPO_COMMANDS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.attributesFile=/dev/null",
    f"--attr-source={_EMPTY_TREE}",
)
_GIT_ENV = {"GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


class GitDiffError(ValueError):
    """The git source cannot be turned into pages."""


def _strip_prefix(raw: str) -> str:
    # `git diff` appends a tab after paths containing spaces.
    name = raw.rstrip("\t").strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    return name[2:] if name[:2] in ("a/", "b/") else name


def _path_of(chunk: str) -> str | None:
    old = new = renamed_to = None
    for line in chunk.split("\n"):
        if line.startswith("@@"):
            break
        if line.startswith("rename to "):
            renamed_to = _strip_prefix(line[len("rename to ") :])
        elif line.startswith("--- "):
            old = _strip_prefix(line[4:])
        elif line.startswith("+++ "):
            new = _strip_prefix(line[4:])
    path = new if new and new != _NO_FILE else old
    if not path or path == _NO_FILE:
        # A rename without edits has no `---`/`+++` lines.
        path = renamed_to
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


def _git(directory: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    """Run git in `directory` with every repo-named command shut off (see above).

    Raises FileNotFoundError and subprocess.TimeoutExpired like `subprocess.run`.
    """
    command = ["git", "-C", str(directory), *_NO_REPO_COMMANDS, *args]
    env = {**os.environ, **_GIT_ENV}
    # Fixed argv, no shell; callers keep a leading "-" out of revisions.
    if text:
        # A file that is not UTF-8 still diffs; its bytes show as U+FFFD.
        return subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    return subprocess.run(  # noqa: S603
        command, capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, check=False, env=env
    )


def _filter_blanks(directory: Path) -> list[str]:
    """`-c` options that blank every filter driver the repo's config (any scope) names."""
    completed = _git(
        directory, "config", "-z", "--name-only", "--get-regexp", r"^filter\.", text=True
    )
    # NUL-separated: a driver name may hold a space.
    keys = [key for key in completed.stdout.split("\0") if key]
    options: list[str] = []
    for name in dict.fromkeys(key.split(".", 1)[1].rsplit(".", 1)[0] for key in keys):
        # `-c` splits at the first "=", so a driver named with one cannot be blanked.
        if "=" in name:
            raise GitDiffError(f"the repo's config has a filter git cannot skip: {name!r}")
        for key in ("clean", "smudge", "process"):
            options += ["-c", f"filter.{name}.{key}="]
        options += ["-c", f"filter.{name}.required=false"]
    return options


def _read_new_side(directory: Path, revision: str, path: str, blanks: list[str]) -> bytes | None:
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
    try:
        completed = _git(directory, *blanks, "show", f"{end}:{path}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _expandable_text(
    directory: Path, revision: str, path: str, diff: str, blanks: list[str]
) -> str | None:
    """The new side's text if it is small, text, and exactly what `diff` was made from."""
    data = _read_new_side(directory, revision, path, blanks)
    if data is None or len(data) > MAX_FILE_BYTES or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if expansion_lines(diff, text) is not None else None


def repo_root(repo: str) -> Path:
    """The top of the work tree `repo` is in: what `git diff` paths are relative to.

    `repo` itself when git cannot say, so a caller still gets a directory to resolve in.
    """
    directory = Path(repo).expanduser()
    try:
        completed = _git(directory, "rev-parse", "--show-toplevel", text=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return directory
    top = completed.stdout.strip()
    return Path(top) if completed.returncode == 0 and top else directory


def pages_from_git(
    repo: str, git_range: str | None, paths: list[str] | None, context_lines: int
) -> list[dict[str, str]]:
    """One `{"path", "diff"}` per changed file of `git diff <git_range>` in `repo`.

    `git_range` defaults to `HEAD` (tracked changes in the working tree). A renamed file
    is one page under its new path; one renamed without edits gets a hunk header that says
    so, since a page needs one. A page whose new-side file can be shown in full also
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

    try:
        blanks = _filter_blanks(directory)
        completed = _git(
            directory,
            *blanks,
            "-c",
            "core.quotepath=off",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            # A submodule diff runs a child git with the submodule's own config, which
            # none of the options here reach.
            "--ignore-submodules=all",
            "--find-renames",
            f"-U{context_lines}",
            revision,
            "--",
            *(paths or []),
            text=True,
        )
    except FileNotFoundError as exc:
        raise GitDiffError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitDiffError(f"git diff took over {_GIT_TIMEOUT_SECONDS}s") from exc
    if completed.returncode != 0:
        if "attr-source" in completed.stderr:
            raise GitDiffError("git 2.40 or newer is needed to diff without running repo commands")
        raise GitDiffError(f"git diff failed: {completed.stderr.strip()}")

    # Paths in the diff are relative to the top of the work tree, not to `repo`.
    top = repo_root(repo)
    starts = [match.start() for match in _FILE_HEADER.finditer(completed.stdout)]
    ends = [*starts[1:], len(completed.stdout)][: len(starts)]
    chunks = [completed.stdout[start:end] for start, end in zip(starts, ends, strict=True)]
    pages: list[dict[str, str]] = []
    budget = MAX_TOTAL_BYTES
    for chunk in chunks:
        path = _path_of(chunk)
        pure_rename = "\nrename from " in chunk and "\n@@ " not in chunk
        if path is not None and pure_rename:
            pages.append({"path": path, "diff": chunk.rstrip("\n") + "\n" + _RENAME_ONLY_HUNK})
        elif path is not None and "\n@@ " in chunk:
            page = {"path": path, "diff": chunk}
            text = _expandable_text(top, revision, path, chunk, blanks)
            if text is not None and len(text.encode("utf-8")) <= budget:
                budget -= len(text.encode("utf-8"))
                page["text"] = text
            pages.append(page)
    if not pages:
        raise GitDiffError(f"no text changes in `git diff {revision}`")
    return pages

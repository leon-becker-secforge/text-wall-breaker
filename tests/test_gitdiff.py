import subprocess
from pathlib import Path

import pytest

from pretty_prompt.gitdiff import GitDiffError, pages_from_git
from pretty_prompt.review import Page
from pretty_prompt.server import _ask_timeout


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    (tmp_path / "gone.txt").write_text("bye\n")
    (tmp_path / "img.bin").write_bytes(b"\x00\x01")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "a.txt").write_text("one\nTWO\nthree\n")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "new file.txt").write_text("hello\n")
    (tmp_path / "img.bin").write_bytes(b"\x00\x02")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "change")
    return tmp_path


def test_one_valid_page_per_text_file_and_binary_is_skipped(repo):
    pages = pages_from_git(str(repo), "HEAD~1..HEAD", None, 3)
    assert sorted(p["path"] for p in pages) == ["a.txt", "gone.txt", "new file.txt"]
    for entry in pages:
        Page(**entry).validated(label=entry["path"])


def test_paths_limit_the_diff(repo):
    pages = pages_from_git(str(repo), "HEAD~1..HEAD", ["a.txt"], 0)
    assert [p["path"] for p in pages] == ["a.txt"]
    assert "\n one" not in pages[0]["diff"]


def test_default_range_is_uncommitted_changes(repo):
    (repo / "a.txt").write_text("changed\n")
    pages = pages_from_git(str(repo), None, None, 3)
    assert [p["path"] for p in pages] == ["a.txt"]


@pytest.mark.parametrize("git_range", ["--output=/tmp/x", "nope..HEAD"])
def test_bad_range_is_refused(repo, git_range):
    with pytest.raises(GitDiffError):
        pages_from_git(str(repo), git_range, None, 3)


def test_empty_diff_and_missing_repo_are_refused(repo, tmp_path):
    with pytest.raises(GitDiffError, match="no text changes"):
        pages_from_git(str(repo), "HEAD", None, 3)
    with pytest.raises(GitDiffError, match="not a directory"):
        pages_from_git(str(tmp_path / "missing"), None, None, 3)


def test_ask_timeout_defaults():
    assert _ask_timeout(None, None, None) == 300.0
    assert _ask_timeout(None, [], None) == 1800.0
    assert _ask_timeout(None, None, "r") == 1800.0
    assert _ask_timeout(42.0, [], None) == 42.0

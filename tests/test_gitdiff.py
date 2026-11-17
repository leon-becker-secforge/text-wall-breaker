import subprocess
from pathlib import Path

import pytest

from text_wall_breaker import gitdiff
from text_wall_breaker.diff import file_status
from text_wall_breaker.gitdiff import GitDiffError, _end_revision, pages_from_git
from text_wall_breaker.review import Page


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [
            *["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"],
            *["-c", "commit.gpgsign=false", *args],  # a scratch repo needs no signing key
        ],
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


@pytest.mark.parametrize(
    ("revision", "end"),
    [
        ("HEAD~1..HEAD", "HEAD"),
        ("main...feature", "feature"),
        ("abc..", "HEAD"),
        ("abc^!", "abc"),
        ("HEAD", None),
        ("v1.0", None),
    ],
)
def test_the_new_side_of_a_range_is_its_end(revision, end):
    assert _end_revision(revision) == end


def _by_path(pages):
    return {page["path"]: page for page in pages}


def test_a_range_carries_the_end_revisions_file_not_the_working_tree(repo):
    (repo / "a.txt").write_text("uncommitted\n")
    pages = _by_path(pages_from_git(str(repo), "HEAD~1..HEAD", None, 3))
    assert pages["a.txt"]["text"] == "one\nTWO\nthree\n"


def test_the_working_tree_diff_carries_the_working_tree_file(repo):
    (repo / "a.txt").write_text("one\ntwo\nthree\nfour\n")
    [page] = pages_from_git(str(repo), None, None, 3)
    assert page["text"] == "one\ntwo\nthree\nfour\n"


def test_a_deleted_file_has_no_text_to_expand(repo):
    pages = _by_path(pages_from_git(str(repo), "HEAD~1..HEAD", None, 3))
    assert "text" not in pages["gone.txt"]


def test_a_file_over_the_size_cap_is_not_expandable(repo, monkeypatch):
    monkeypatch.setattr(gitdiff, "MAX_FILE_BYTES", 5)
    pages = pages_from_git(str(repo), "HEAD~1..HEAD", None, 3)
    assert all("text" not in page for page in pages)


def test_files_past_the_total_budget_are_not_expandable(repo, monkeypatch):
    # a.txt (14 bytes) fits, the next file no longer does
    monkeypatch.setattr(gitdiff, "MAX_TOTAL_BYTES", 14)
    pages = pages_from_git(str(repo), "HEAD~1..HEAD", None, 3)
    assert [page["path"] for page in pages if "text" in page] == ["a.txt"]


def test_a_file_that_is_not_utf8_is_not_expandable(repo):
    (repo / "a.txt").write_bytes(b"one\nT\xffO\nthree\n")
    [page] = pages_from_git(str(repo), None, None, 3)
    assert "text" not in page


def test_a_symlink_is_never_followed_out_of_the_repo(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("one\nTWO\nthree\nsecret\n")
    (repo / "a.txt").unlink()
    (repo / "a.txt").symlink_to(outside)
    pages = pages_from_git(str(repo), None, None, 3)
    assert pages
    assert all("text" not in page for page in pages)


def test_a_renamed_file_is_one_page_under_its_new_path(tmp_path):
    _git(tmp_path, "init", "-q")
    body = "".join(f"line {n}\n" for n in range(30))
    (tmp_path / "old.txt").write_text(body)
    (tmp_path / "same.txt").write_text(body + "extra\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "old.txt").rename(tmp_path / "new.txt")
    (tmp_path / "new.txt").write_text(body.replace("line 3\n", "line three\n"))
    (tmp_path / "same.txt").rename(tmp_path / "moved.txt")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move")

    pages = _by_path(pages_from_git(str(tmp_path), "HEAD~1..HEAD", None, 3))
    assert sorted(pages) == ["moved.txt", "new.txt"]
    assert file_status(pages["new.txt"]["diff"]) == ("R", "old.txt")
    # Renamed without edits: still a valid page, with a hunk header that says why it is bare.
    assert file_status(pages["moved.txt"]["diff"]) == ("R", "same.txt")
    assert "renamed without changes" in pages["moved.txt"]["diff"]
    for entry in pages.values():
        Page(**entry).validated(label=entry["path"])


def test_a_file_that_changed_since_the_diff_is_not_expandable(repo, monkeypatch):
    monkeypatch.setattr(gitdiff, "_read_new_side", lambda d, r, p: b"something else entirely\n")
    pages = pages_from_git(str(repo), "HEAD~1..HEAD", None, 3)
    assert all("text" not in page for page in pages)

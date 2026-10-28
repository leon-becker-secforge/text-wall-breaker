from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pretty_prompt import opener as opener_module
from pretty_prompt.opener import BrowserUnavailable, Opener, find_opener


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PRETTY_PROMPT_OPENER", raising=False)
    monkeypatch.setattr(opener_module, "is_wsl", lambda: False)


def _which_only(*programs: str):
    return lambda name: f"/bin/{name}" if name in programs else None


def _record_runs(monkeypatch, *, returncode: int = 0, stdout: str = ""):
    """Replace subprocess.run with one that records each argv and returns `returncode`.

    `wslpath` always succeeds with `stdout`; `returncode` is the opener's own status.
    """
    runs: list[list[str]] = []

    def fake_run(argv, **kwargs):
        runs.append(argv)
        if argv[0] == "wslpath":
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    return runs


def test_override_wins_and_skips_the_display_check(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", _which_only("firefox"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("PRETTY_PROMPT_OPENER", "firefox --new-tab")
    assert find_opener() == Opener(name="firefox", argv=("/bin/firefox", "--new-tab"))


def test_override_appends_the_target_last(monkeypatch):
    monkeypatch.setattr("shutil.which", _which_only("firefox"))
    monkeypatch.setenv("PRETTY_PROMPT_OPENER", "firefox --new-tab")
    runs = _record_runs(monkeypatch)
    opener = find_opener()
    assert isinstance(opener, Opener)
    opener.open("http://127.0.0.1:1/?t=x")
    assert runs == [["/bin/firefox", "--new-tab", "http://127.0.0.1:1/?t=x"]]


@pytest.mark.parametrize(
    ("value", "reason"),
    [("nope --flag", "not on PATH"), ("   ", "empty"), ('firefox "unclosed', "invalid")],
)
def test_bad_override_is_refused(monkeypatch, value, reason):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", _which_only("firefox"))
    monkeypatch.setenv("PRETTY_PROMPT_OPENER", value)
    problem = find_opener()
    assert isinstance(problem, str)
    assert "PRETTY_PROMPT_OPENER" in problem
    assert reason in problem


def test_macos_uses_open(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which", _which_only("open"))
    monkeypatch.delenv("DISPLAY", raising=False)
    assert find_opener() == Opener(name="open", argv=("/bin/open",))


def test_windows_uses_startfile(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    opened: list[str] = []
    monkeypatch.setattr(opener_module.os, "startfile", opened.append, raising=False)
    opener = find_opener()
    assert opener == Opener(name="os.startfile")
    assert isinstance(opener, Opener)
    opener.open(Path("C:/tmp/page.html"))
    assert opened == [str(Path("C:/tmp/page.html"))]


def test_windows_startfile_failure_is_browser_unavailable(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")

    def refuse(target):
        raise OSError("no association")

    monkeypatch.setattr(opener_module.os, "startfile", refuse, raising=False)
    with pytest.raises(BrowserUnavailable, match="no association"):
        Opener(name="os.startfile").open("http://127.0.0.1:1/")


def test_wsl_prefers_wslview_and_needs_no_display(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(opener_module, "is_wsl", lambda: True)
    monkeypatch.setattr("shutil.which", _which_only("wslview", "explorer.exe", "xdg-open"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert find_opener() == Opener(name="wslview", argv=("/bin/wslview",))


def test_wsl_falls_back_to_explorer_with_translated_paths(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(opener_module, "is_wsl", lambda: True)
    monkeypatch.setattr("shutil.which", _which_only("explorer.exe"))
    opener = find_opener()
    assert isinstance(opener, Opener)
    # explorer.exe exits 1 on success, so that must not count as a failure.
    runs = _record_runs(
        monkeypatch, returncode=1, stdout="\\\\wsl.localhost\\Ubuntu\\home\\me\\p.html\n"
    )
    opener.open(Path("/home/me/p.html"))
    assert runs == [
        ["wslpath", "-w", "/home/me/p.html"],
        ["/bin/explorer.exe", "\\\\wsl.localhost\\Ubuntu\\home\\me\\p.html"],
    ]


def test_wsl_passes_urls_to_explorer_untranslated(monkeypatch):
    runs = _record_runs(monkeypatch, returncode=1)
    opener = Opener(
        name="explorer.exe", argv=("/bin/explorer.exe",), check_exit=False, windows_paths=True
    )
    opener.open("http://127.0.0.1:1/?t=x")
    assert runs == [["/bin/explorer.exe", "http://127.0.0.1:1/?t=x"]]


def test_wsl_without_interop_is_refused(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(opener_module, "is_wsl", lambda: True)
    monkeypatch.setattr("shutil.which", _which_only("xdg-open"))
    problem = find_opener()
    assert isinstance(problem, str)
    assert "WSL" in problem


def test_failing_exit_status_is_browser_unavailable(monkeypatch):
    _record_runs(monkeypatch, returncode=3)
    with pytest.raises(BrowserUnavailable, match="exit status 3"):
        Opener(name="xdg-open", argv=("/bin/xdg-open",)).open("http://127.0.0.1:1/")


def test_hanging_opener_is_browser_unavailable(monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr("subprocess.run", hang)
    with pytest.raises(BrowserUnavailable, match="did not return"):
        Opener(name="xdg-open", argv=("/bin/xdg-open",)).open("http://127.0.0.1:1/")


def test_wsl_is_detected_from_the_distro_variable(monkeypatch):
    monkeypatch.undo()  # the autouse fixture stubbed is_wsl out
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert opener_module.is_wsl()


def test_linux_desktop_tries_webbrowser_before_xdg_open(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", _which_only("xdg-open"))
    monkeypatch.setenv("DISPLAY", ":0")
    opener = find_opener()
    assert isinstance(opener, Opener)
    assert opener.try_webbrowser
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    runs = _record_runs(monkeypatch)
    opener.open(Path("/tmp/p.html"))  # noqa: S108 -- never touches the filesystem, webbrowser.open is mocked
    assert opened == ["file:///tmp/p.html"]
    assert runs == []


def test_xdg_open_is_the_fallback_when_webbrowser_finds_no_browser(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: False)
    runs = _record_runs(monkeypatch)
    Opener(name="xdg-open", argv=("/bin/xdg-open",), try_webbrowser=True).open(
        "http://127.0.0.1:1/"
    )
    assert runs == [["/bin/xdg-open", "http://127.0.0.1:1/"]]

"""Which program hands a page to the user's browser, per platform.

`find_opener` picks one, in this order:

  1. `TEXT_WALL_BREAKER_OPENER`, if set: a command line the target is appended to as its
     last argument. It wins over everything below and skips the display check, since
     whoever set it knows their desktop better than a heuristic does.
  2. Windows: `os.startfile`, the shell's own "open with the default handler".
  3. macOS: `open`.
  4. WSL: the Windows browser, not a Linux one inside WSLg, since that is the browser
     the person actually uses. `wslview` (from `wslu`) if it is installed, otherwise
     `explorer.exe` (on `PATH`, else at `/mnt/c/Windows/explorer.exe`) with a file target
     translated to its `\\\\wsl.localhost\\...` path.
     The loopback URLs work as-is, since WSL2 forwards `127.0.0.1` to Windows.
  5. Any other POSIX desktop: `webbrowser.open`, then `xdg-open` if that finds no
     browser. Only with `DISPLAY` or `WAYLAND_DISPLAY` set; a headless host gets a reason
     instead of an opener.

Resolved on every call rather than cached: it is a `which` and an environment lookup,
and a cache would outlive a test's monkeypatching.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path

from .log import logger

__all__ = ["OPENER_ENV_VAR", "BrowserUnavailable", "Opener", "find_opener", "is_wsl"]

OPENER_ENV_VAR = "TEXT_WALL_BREAKER_OPENER"

# Where WSL mounts the Windows system directory by default. `appendWindowsPath = false` in
# /etc/wsl.conf keeps it off PATH while interop still works, so `which` alone misses it.
WINDOWS_EXPLORER = "/mnt/c/Windows/explorer.exe"

# Every opener here hands the target to the desktop and returns immediately. If one has
# not returned by now something is wrong with the desktop session, and failing is better
# than blocking for the caller's whole timeout with no window on screen.
_OPEN_TIMEOUT_SECONDS = 20.0


class BrowserUnavailable(RuntimeError):
    """The page was written but no browser could be opened for it."""


@dataclass(frozen=True)
class Opener:
    """A resolved way to open a page. `argv` empty means `os.startfile` (Windows)."""

    name: str
    argv: tuple[str, ...] = ()
    # explorer.exe exits 1 even when it opened the page, so its status says nothing.
    # A page that really never opened still surfaces, as `not_delivered`.
    check_exit: bool = True
    # Translate a file target with `wslpath -w` first: a Windows program can't open
    # `/tmp/...`, only the `\\wsl.localhost\...` path WSL exports it under.
    windows_paths: bool = False
    # `xdg-open` runs a browser that is not yet running in the foreground, so a cold start
    # never returns and hits the timeout with the page already on screen. `webbrowser`
    # gives a Firefox or Chrome launcher at most 5 seconds and then counts it as started.
    # It returns False, with no reason, when it finds no browser; `argv` is the fallback.
    try_webbrowser: bool = False

    def open(self, target: Path | str) -> None:
        """Hand `target` to the browser, raising `BrowserUnavailable` if it refuses.

        `target` is a `Path` for a one-shot page, or a `http://127.0.0.1:...` URL for a
        reused-tab session (see `SessionListener`).
        """
        if not self.argv:
            _startfile(target, self.name)
            return
        if self.try_webbrowser and webbrowser.open(_as_url(target)):
            return
        arg = _windows_path(target) if self.windows_paths else str(target)
        result = _run([*self.argv, arg], self.name)
        logger.info(
            "opener %s %s exited %s%s",
            self.name,
            [*self.argv, arg],
            result.returncode,
            f": {result.stderr.strip()}" if result.stderr.strip() else "",
        )
        if self.check_exit and result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise BrowserUnavailable(f"{self.name} failed: {detail}")
        if not self.check_exit:
            self._log_unchecked(result)

    def _log_unchecked(self, result: subprocess.CompletedProcess[str]) -> None:
        """Leave a trace on stderr (the client's MCP log) of an opener whose status is ignored.

        Status 0 and 1 with no output are what explorer.exe prints on success, so they
        stay quiet; anything else is the only clue when no tab appears and the call ends
        as `not_delivered`.
        """
        stderr = result.stderr.strip()
        if result.returncode not in (0, 1) or stderr:
            logger.warning(
                "opener %s exited %s%s",
                self.name,
                result.returncode,
                f": {stderr}" if stderr else "",
            )


def find_opener() -> Opener | str:
    """Return the opener for this host, or why this host can't show a page."""
    override = os.environ.get(OPENER_ENV_VAR)
    if override:
        return _parse_override(override)
    if sys.platform == "win32":
        return Opener(name="os.startfile")
    if sys.platform == "darwin":
        return _on_path("open") or "`open` is not on PATH"
    if is_wsl():
        wslview = _on_path("wslview")
        if wslview is not None:
            return wslview
        explorer = shutil.which("explorer.exe")
        if explorer is None and Path(WINDOWS_EXPLORER).is_file():
            explorer = WINDOWS_EXPLORER
        if explorer is None:
            return (
                "running under WSL, but neither wslview nor explorer.exe was found: not on "
                f"PATH and not at {WINDOWS_EXPLORER}. Windows interop may be disabled, or "
                "`appendWindowsPath = false` in /etc/wsl.conf keeps the Windows directories "
                f"off PATH; set {OPENER_ENV_VAR} to override"
            )
        return Opener(name="explorer.exe", argv=(explorer,), check_exit=False, windows_paths=True)
    xdg_open = _on_path("xdg-open")
    if xdg_open is None:
        return "xdg-open is not on PATH; text-wall-breaker needs it to open the question page"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return (
            "neither DISPLAY nor WAYLAND_DISPLAY is set, so there is no desktop to open a page on"
        )
    return replace(xdg_open, try_webbrowser=True)


def is_wsl() -> bool:
    """Whether this is Linux running under WSL (1 or 2)."""
    if sys.platform != "linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False


def _parse_override(value: str) -> Opener | str:
    try:
        argv = shlex.split(value, posix=sys.platform != "win32")
    except ValueError as exc:
        return f"{OPENER_ENV_VAR} is invalid: {exc}"
    if not argv:
        return f"{OPENER_ENV_VAR} is set but empty"
    executable = shutil.which(argv[0])
    if executable is None:
        return f"{OPENER_ENV_VAR} names {argv[0]!r}, which is not on PATH"
    return Opener(name=argv[0], argv=(executable, *argv[1:]))


def _on_path(program: str) -> Opener | None:
    executable = shutil.which(program)
    return None if executable is None else Opener(name=program, argv=(executable,))


def _as_url(target: Path | str) -> str:
    return target.as_uri() if isinstance(target, Path) else target


def _startfile(target: Path | str, name: str) -> None:
    # The platform check is what lets a type checker running on Linux accept the call.
    if sys.platform != "win32":
        raise BrowserUnavailable(f"{name} is only available on Windows")
    try:
        os.startfile(str(target))  # noqa: S606 — the shell's default handler, by design
    except OSError as exc:
        raise BrowserUnavailable(f"{name} failed: {exc}") from exc


def _windows_path(target: Path | str) -> str:
    if not isinstance(target, Path):
        return target
    result = _run(["wslpath", "-w", str(target)], "wslpath")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise BrowserUnavailable(f"wslpath failed: {detail}")
    return result.stdout.strip()


def _run(argv: list[str], name: str) -> subprocess.CompletedProcess[str]:
    try:
        # Fixed argv, no shell, and the only caller-influenced part is a path this
        # process just wrote into its own temp directory, or a loopback URL it owns.
        return subprocess.run(  # noqa: S603 — resolved executable, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=_OPEN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserUnavailable(f"{name} did not return") from exc
    except OSError as exc:
        raise BrowserUnavailable(f"{name} could not be run: {exc}") from exc

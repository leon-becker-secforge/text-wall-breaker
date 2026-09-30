"""Which program hands a page to the user's browser, per platform.

`find_opener` picks one, in this order:

  1. `PRETTY_PROMPT_OPENER`, if set: a command line the target is appended to as its
     last argument. It wins over everything below and skips the display check, since
     whoever set it knows their desktop better than a heuristic does.
  2. Windows: `os.startfile`, the shell's own "open with the default handler".
  3. macOS: `open`.
  4. WSL: the Windows browser, not a Linux one inside WSLg, since that is the browser
     the person actually uses. `wslview` (from `wslu`) if it is installed, otherwise
     `explorer.exe` with a file target translated to its `\\\\wsl.localhost\\...` path.
     The loopback URLs work as-is, since WSL2 forwards `127.0.0.1` to Windows.
  5. Any other POSIX desktop: `xdg-open`, and only with `DISPLAY` or `WAYLAND_DISPLAY`
     set. A headless host gets a reason instead of an opener.

Resolved on every call rather than cached: it is a `which` and an environment lookup,
and a cache would outlive a test's monkeypatching.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["OPENER_ENV_VAR", "BrowserUnavailable", "Opener", "find_opener", "is_wsl"]

OPENER_ENV_VAR = "PRETTY_PROMPT_OPENER"

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

    def open(self, target: Path | str) -> None:
        """Hand `target` to the browser, raising `BrowserUnavailable` if it refuses.

        `target` is a `Path` for a one-shot page, or a `http://127.0.0.1:...` URL for a
        reused-tab session (see `SessionListener`).
        """
        if not self.argv:
            _startfile(target, self.name)
            return
        arg = _windows_path(target) if self.windows_paths else str(target)
        result = _run([*self.argv, arg], self.name)
        if self.check_exit and result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise BrowserUnavailable(f"{self.name} failed: {detail}")


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
        if explorer is None:
            return (
                "running under WSL, but neither wslview nor explorer.exe is on PATH "
                f"(Windows interop may be disabled); set {OPENER_ENV_VAR} to override"
            )
        return Opener(name="explorer.exe", argv=(explorer,), check_exit=False, windows_paths=True)
    xdg_open = _on_path("xdg-open")
    if xdg_open is None:
        return "xdg-open is not on PATH — pretty-prompt needs it to open the question page"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return "neither DISPLAY nor WAYLAND_DISPLAY is set — there is no desktop to open a page on"
    return xdg_open


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

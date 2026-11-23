"""File logging in a fixed place, so a failed call can be diagnosed afterwards.

The server's stderr lands in the MCP client's own per-session log, which neither the
user nor an agent knows to look in. This writes `text-wall-breaker.log` under
`$XDG_STATE_HOME/text-wall-breaker/` (default `~/.local/state/text-wall-breaker/`), private
to the user, and rotated at server start once past 1 MB, keeping two backups.
`--log-level` sets the level (default `info`) or `off`.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

__all__ = ["active_log", "configure", "log_path", "logger"]

logger = logging.getLogger("text_wall_breaker")

_MAX_BYTES = 1_000_000
_BACKUPS = 2

# A page URL carries the session token, which grants its holder the page's answers; it
# may also appear percent-encoded, as in an asset URL's `path` parameter.
_TOKEN = re.compile(r"t(?:=|%3[dD])[0-9a-f]{32}\b")

_active: Path | None = None


def log_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "text-wall-breaker" / "text-wall-breaker.log"


def active_log() -> Path | None:
    """The file `configure` attached, or None if logging is off or failed."""
    return _active


class _RedactingFormatter(logging.Formatter):
    """Formats a record, traceback and stack included, with every session token cut out.

    A formatter rather than a filter: it sees the finished text, exception text included,
    and runs inside the handler, whose error handling covers a bad format string.
    """

    def format(self, record: logging.LogRecord) -> str:
        return _TOKEN.sub("t=…", super().format(record))


def _rotate(path: Path) -> None:
    # At start only: every MCP client session runs its own server on this one file, and a
    # rename while another process writes is not safe (and fails on Windows).
    if not path.exists() or path.stat().st_size <= _MAX_BYTES:
        return
    for index in range(_BACKUPS, 0, -1):
        source = path.with_name(f"{path.name}.{index - 1}") if index > 1 else path
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index}"))


def configure(level_name: str = "info") -> Path | None:
    """Attach the file handler at `level_name` (`config.LOG_LEVELS`); None if logging is off.

    Returns the log file's path.
    """
    global _active
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    _active = None
    if level_name == "off":
        return None
    level = logging.getLevelNamesMapping()[level_name.upper()]
    path = log_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        try:
            _rotate(path)
        except OSError as exc:
            print(f"text-wall-breaker: cannot rotate log {path}: {exc}", file=sys.stderr)
        path.touch(mode=0o600)
        path.chmod(0o600)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        # Logging must never stop the server; stderr is the fallback.
        print(f"text-wall-breaker: cannot write log {path}: {exc}", file=sys.stderr)
        return None
    handler.setFormatter(
        _RedactingFormatter("%(asctime)s pid=%(process)d %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    _active = path
    return path

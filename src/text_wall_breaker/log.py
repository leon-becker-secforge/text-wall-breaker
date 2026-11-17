"""File logging in a fixed place, so a failed call can be diagnosed afterwards.

The server's stderr lands in the MCP client's own per-session log, which neither the
user nor an agent knows to look in. This writes `text-wall-breaker.log` under
`$XDG_STATE_HOME/text-wall-breaker/` (default `~/.local/state/text-wall-breaker/`), rotated at
1 MB with two backups. `TEXT_WALL_BREAKER_LOG` sets the level (default `info`) or `off`.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

__all__ = ["LOG_ENV_VAR", "configure", "log_path", "logger"]

LOG_ENV_VAR = "TEXT_WALL_BREAKER_LOG"

logger = logging.getLogger("text_wall_breaker")


def log_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "text-wall-breaker" / "text-wall-breaker.log"


def configure() -> Path | None:
    """Attach the file handler once; returns its path, or None if logging is off."""
    level_name = os.environ.get(LOG_ENV_VAR, "info").strip().lower()
    if level_name == "off":
        return None
    level = logging.getLevelNamesMapping().get(level_name.upper(), logging.INFO)
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError as exc:
        # Logging must never stop the server; stderr is the fallback.
        print(f"text-wall-breaker: cannot write log {path}: {exc}", file=sys.stderr)
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s pid=%(process)d %(levelname)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return path

"""The server's command-line options, parsed once by `server.main`.

Options are command-line arguments rather than environment variables: an MCP client's
config names them in the server's `args`, and argparse refuses a misspelled one at
launch, where a misspelled variable name would be ignored without a word.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from .page import InvalidAccent, parse_accent

__all__ = ["LOG_LEVELS", "Config", "current", "parse_args"]

LOG_LEVELS = ("off", "debug", "info", "warning", "error", "critical")


@dataclass(frozen=True)
class Config:
    opener: str | None = None
    accent: str | None = None
    log_level: str = "info"
    glossary: bool = True


# Read at call time as `config.current`, never imported by name: `server.main` replaces
# it after the importing modules have loaded, and tests replace it with monkeypatch.
current = Config()


def _accent(value: str) -> str:
    try:
        parse_accent(value)
    except InvalidAccent as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="text-wall-breaker-mcp",
        description="MCP server that shows questions, results and diffs as browser pages.",
    )
    result.add_argument(
        "--opener",
        metavar="COMMAND",
        help=(
            "command line to open pages with instead of the platform default; the page's "
            "path or URL is appended as the last argument. Split shell-style, not run "
            "through a shell. Skips the display check."
        ),
    )
    result.add_argument(
        "--accent",
        metavar="#RRGGBB",
        type=_accent,
        help="accent colour of every page, in light and dark theme (default: green)",
    )
    result.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="info",
        help="level of the log file, or off for none (default: info)",
    )
    result.add_argument(
        "--no-glossary",
        dest="glossary",
        action="store_false",
        help="don't define the project's glossary terms on ask pages",
    )
    return result


def parse_args(argv: Sequence[str] | None = None) -> Config:
    """Parse `argv` (default: `sys.argv[1:]`); exits with status 2 on a bad option."""
    namespace = parser().parse_args(argv)
    return Config(
        opener=namespace.opener,
        accent=namespace.accent,
        log_level=namespace.log_level,
        glossary=namespace.glossary,
    )

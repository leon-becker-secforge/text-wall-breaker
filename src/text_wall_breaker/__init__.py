"""An MCP server that asks the user questions in a browser page.

The server itself is `text_wall_breaker.server`; it is not imported here, so importing any
other module does not build it.
"""

from __future__ import annotations

from .ask import AskRequest, BrowserUnavailable, InvalidAsk, Option, Question, run_ask

__all__ = [
    "AskRequest",
    "BrowserUnavailable",
    "InvalidAsk",
    "Option",
    "Question",
    "run_ask",
]

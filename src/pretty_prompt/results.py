"""The shapes the tools return, so the MCP client gets an `outputSchema` for each.

Plain `TypedDict`s with every key optional, not models: a model would fill the keys a
result lacks with `null`, whereas a `TypedDict` leaves them out. A key missing from a
declaration here is dropped from the structured result (the text result still has it),
so `tests/test_results.py` checks that every key the code produces is declared. Nested
entries stay `dict[str, Any]`: their keys vary with the question or the diff.
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = ["AskResult", "LiveResult", "ReviewResult", "ShowResult", "ShowStatusResult"]


class AskResult(TypedDict, total=False):
    # An answer to one question, or `answers` for `questions`.
    id: str
    selected: list[str]
    free_text: str
    expanded: list[str]
    changed_from: list[str]
    visits: int
    answers: list[dict[str, Any]]
    # Outcomes other than an answer.
    cancelled: bool
    abandoned: bool
    not_delivered: bool
    reason: str
    interrupted: bool
    resume: str
    hint: str
    # A live session started instead of an answer.
    live: dict[str, str]
    instruction: str


class ReviewResult(TypedDict, total=False):
    comments: list[dict[str, Any]]
    page_notes: list[dict[str, Any]]
    overall_note: str | None
    cancelled: bool
    abandoned: bool
    not_delivered: bool
    live: dict[str, str]
    instruction: str


class ShowResult(TypedDict, total=False):
    opened: bool
    path: str
    acknowledged: bool
    abandoned: bool
    not_delivered: bool


class ShowStatusResult(TypedDict, total=False):
    state: str
    title: str
    own_tab: str


class LiveResult(AskResult, ReviewResult, total=False):
    """A `live` call returns what the user sent, or, once the session ends, the result of
    the `ask` or `review` that started it plus what was said in the session."""

    messages: list[dict[str, Any]]
    posted: bool
    refreshed: dict[str, Any]
    refresh_dropped: bool
    chat: list[dict[str, Any]]
    threads: list[dict[str, Any]]

from __future__ import annotations

import anyio

from text_wall_breaker import ask as ask_module
from text_wall_breaker.ask import Option, Question
from text_wall_breaker.listener import Submission
from text_wall_breaker.results import AskResult, LiveResult, ReviewResult, ShowStatusResult
from text_wall_breaker.review import Page, _result_from_submission
from text_wall_breaker.server import server


def declared(shape: type) -> set[str]:
    return set(shape.__annotations__)


def test_every_tool_publishes_an_output_schema():
    tools = anyio.run(server.list_tools)
    missing = [t.name for t in tools if not (t.output_schema or {}).get("properties")]
    assert missing == []


def test_ask_answers_use_only_declared_keys():
    step = Question(question="Q", id="q", options=[Option(id="a", label="A")])
    raw = {"selected": ["a"], "free_text": "x", "expanded": ["a"], "visits": 2}
    single = ask_module._extract_answer(step, raw)
    multi = ask_module._extract_answer(step, raw, multi=True)
    interrupted = ask_module._interrupted(
        [step], {"answers": [raw], "interrupted": 0}, multi=True, header=""
    )
    assert set(single) | set(multi) | set(interrupted) | set(
        ask_module.not_delivered()
    ) <= declared(AskResult)
    assert set(interrupted["answers"][0]) <= declared(AskResult) | {"interrupted"}


def test_review_results_use_only_declared_keys():
    pages = [Page(path="a.txt", diff="@@ -1 +1 @@\n-a\n+b\n")]
    result = _result_from_submission(pages, Submission(kind="cancel", data={}), None)
    assert set(result) <= declared(ReviewResult)


def test_live_and_status_keys_are_declared_by_their_shapes():
    assert {"messages", "posted", "refreshed", "refresh_dropped", "chat", "threads"} <= declared(
        LiveResult
    )
    assert declared(AskResult) <= declared(LiveResult)
    assert declared(ReviewResult) <= declared(LiveResult)
    assert {"state", "title", "own_tab"} == declared(ShowStatusResult)

from __future__ import annotations

import threading
import time

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import GetPromptResult, TextContent
from support import make_live_review as make_live
from support import wait_until

import text_wall_breaker.server as server_module
from text_wall_breaker.config import Config
from text_wall_breaker.listener import LoopbackListener, SessionListener
from text_wall_breaker.server import (
    MAX_TOOL_DESCRIPTION_CHARS,
    environment_problem,
    server,
)


class _ProgressRecorder:
    def __init__(self):
        self.beats: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        self.beats.append((progress, total, message))


def test_a_blocking_call_reports_progress_until_it_returns(monkeypatch):
    monkeypatch.setattr(server_module, "KEEPALIVE_SECONDS", 0.05)
    recorder = _ProgressRecorder()

    def work() -> str:
        wait_until(lambda: len(recorder.beats) >= 3)
        return "done"

    async def main():
        return await server_module._blocking(recorder, work)

    assert anyio.run(main) == "done"
    assert [b[0] for b in recorder.beats] == list(range(1, len(recorder.beats) + 1))
    count = len(recorder.beats)
    anyio.run(anyio.sleep, 0.15)
    assert len(recorder.beats) == count


def test_unknown_tool_arguments_are_an_error():
    async def main():
        return await server.call_tool("live", {"session": "x", "reply": "hi"})

    with pytest.raises(ToolError, match="reply"):
        anyio.run(main)


def test_a_blocking_call_without_a_context_just_runs():
    async def main():
        return await server_module._blocking(None, lambda: 7)

    assert anyio.run(main) == 7


def test_no_tool_exposes_the_context_parameter():
    for tool in anyio.run(server.list_tools):
        assert "ctx" not in tool.input_schema.get("properties", {})


@pytest.fixture(autouse=True)
def _plain_linux(monkeypatch):
    """Pin the environment checks to a non-WSL Linux host with no opener override."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("text_wall_breaker.opener.is_wsl", lambda: False)
    monkeypatch.setattr("text_wall_breaker.config.current", Config())


def test_environment_is_accepted_when_both_checks_pass(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setenv("DISPLAY", ":0")
    assert environment_problem() is None


def test_wayland_alone_is_enough(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert environment_problem() is None


def test_missing_xdg_open_is_refused(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("DISPLAY", ":0")
    assert "xdg-open" in (environment_problem() or "")


def test_headless_is_refused(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert "DISPLAY" in (environment_problem() or "")


def test_ask_show_show_status_review_and_live_are_the_only_registered_tools():
    tools = anyio.run(server.list_tools)
    assert [tool.name for tool in tools] == [
        "ask",
        "show",
        "show_status",
        "review",
        "live",
    ]


def test_no_tool_description_risks_truncation():
    # The MCP client silently truncates a tool's description past this length — a hard
    # prefix cut, mid-sentence, with no signal to the agent that anything was cut. This
    # caught `ask`'s description once (4268 chars) doing exactly that; the tool's whole
    # later sections were invisible to every agent until it was compressed.
    tools = anyio.run(server.list_tools)
    over_limit = {
        tool.name: len(tool.description or "")
        for tool in tools
        if len(tool.description or "") > MAX_TOOL_DESCRIPTION_CHARS
    }
    assert over_limit == {}


def test_ask_schema_matches_the_agreed_shape():
    tool = anyio.run(server.list_tools)[0]
    schema = tool.input_schema
    # No required properties: `question` alone answers the single-question shorthand,
    # `questions` alone answers the multi-question prototype, and either can carry the
    # whole call — schema-level "required" cannot express an either/or across the two.
    assert "required" not in schema
    properties = schema["properties"]
    assert set(properties) >= {
        "question",
        "options",
        "multi_select",
        "allow_free_text",
        "questions",
        "header",
    }
    assert "allow_defer" not in properties
    assert "timeout_seconds" not in properties
    option = schema["$defs"]["Option"]["properties"]
    assert set(option) == {"id", "label", "caveat", "body", "selected", "recommended"}
    question = schema["$defs"]["Question"]["properties"]
    assert set(question) == {
        "free_text",
        "id",
        "question",
        "options",
        "multi_select",
        "allow_free_text",
        "rank",
    }
    # Ranking is for `questions` only: the single-question top level stays without it.
    assert "rank" not in properties


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"question": "q", "options": [], "allow_free_text": False}, "no way to answer"),
        ({"question": "  "}, "question is empty"),
        ({"question": "q", "questions": [{"question": "a"}]}, "not both"),
        ({"questions": []}, "questions is empty"),
        ({"questions": [{"question": " "}]}, r"questions\[0\]: question is empty"),
    ],
)
def test_an_unanswerable_call_fails_before_a_page_is_opened(arguments, match):
    # No browser is stubbed here on purpose: if validation did not run first, the call
    # would try to open a page instead of erroring.
    with pytest.raises(ToolError, match=match):
        anyio.run(lambda: server.call_tool("ask", arguments))


def test_show_status_is_registered_and_takes_no_arguments():
    tool = next(t for t in anyio.run(server.list_tools) if t.name == "show_status")
    assert tool.input_schema.get("properties", {}) == {}


def _undocumented(schema: dict, where: str) -> list[str]:
    """Every property of `schema` and of its `$defs` that has no description."""
    scopes = [(where, schema)]
    scopes += [(f"{where}.{name}", node) for name, node in schema.get("$defs", {}).items()]
    return [
        f"{scope}.{name}"
        for scope, node in scopes
        for name, prop in node.get("properties", {}).items()
        if not prop.get("description")
    ]


def test_every_tool_parameter_is_described():
    missing = [
        path
        for tool in anyio.run(server.list_tools)
        for path in _undocumented(tool.input_schema, tool.name)
    ]
    assert missing == []


def test_an_error_of_a_blocking_call_is_not_wrapped_in_a_group():
    def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        anyio.run(server_module._blocking, _ProgressRecorder(), fail)


def test_an_interrupted_call_sets_its_cancel_event_and_returns_at_once():
    cancel = threading.Event()
    finished = threading.Event()

    def wait():
        cancel.wait(5)
        finished.set()

    async def scenario():
        with anyio.move_on_after(0.2) as scope:
            await server_module._blocking(None, wait, cancel=cancel)
        return scope.cancelled_caught

    assert anyio.run(scenario) is True
    assert cancel.is_set()
    assert finished.wait(2)


def test_an_interrupted_wait_on_a_page_ends_the_wait():
    # ask, review and show pass no event of their own; an interruption must still end the
    # listener's wait, or the call holds its tab and socket until the user answers.
    finished = threading.Event()

    def wait():
        with LoopbackListener("tok") as listener:
            listener.mark_seen()
            listener.wait()
        finished.set()

    async def scenario():
        with anyio.move_on_after(0.2):
            await server_module._blocking(None, wait)

    started = time.monotonic()
    anyio.run(scenario)
    assert finished.wait(2)
    assert time.monotonic() - started < 2


def test_an_interrupted_wait_on_the_shared_tab_ends_the_wait():
    finished = threading.Event()
    session = SessionListener("tok")
    session.publish(lambda generation: "<html>")

    def wait():
        session.wait_for_answer()
        finished.set()

    async def scenario():
        with anyio.move_on_after(0.2):
            await server_module._blocking(None, wait)

    try:
        anyio.run(scenario)
        assert finished.wait(2)
    finally:
        session.close()


def test_a_failing_progress_report_does_not_lose_the_result(monkeypatch):
    monkeypatch.setattr(server_module, "KEEPALIVE_SECONDS", 0.02)

    class Broken:
        async def report_progress(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("stdout closed")

    def answer_later() -> str:
        time.sleep(0.2)
        return "answer"

    async def main():
        return await server_module._blocking(Broken(), answer_later)

    assert anyio.run(main) == "answer"


def test_an_interrupted_live_call_does_not_lock_the_session():
    _, live = make_live()

    async def scenario():
        with anyio.move_on_after(0.3):
            await server.call_tool("live", {"session": live.id})
        await anyio.sleep(1.0)
        return await server.call_tool(
            "live", {"session": live.id, "wait": False, "replies": [{"to": "chat", "text": "hi"}]}
        )

    result = anyio.run(scenario)
    assert result.structured_content == {"posted": True, "messages": []}


def test_a_live_call_error_reaches_the_agent_as_its_own_message():
    _, live = make_live()

    async def scenario():
        await server.call_tool(
            "live", {"session": live.id, "replies": [{"to": "t99", "text": "hi"}]}
        )

    with pytest.raises(ToolError, match="no thread 't99'"):
        anyio.run(scenario)


def test_a_note_side_is_an_enum_in_the_schema():
    review = next(t for t in anyio.run(server.list_tools) if t.name == "review")
    note = review.input_schema["$defs"]["Note"]["properties"]
    assert note["side"]["enum"] == ["new", "old"]


def _prompt_text(**arguments: str) -> str:
    result = anyio.run(server.get_prompt, "pp-review", arguments)
    assert isinstance(result, GetPromptResult)
    content = result.messages[0].content
    assert isinstance(content, TextContent)
    return content.text


def test_pp_review_prompt_is_listed_and_asks_for_a_review_with_notes():
    [prompt] = anyio.run(server.list_prompts)
    assert prompt.name == "pp-review"
    assert [arg.name for arg in prompt.arguments or []] == ["base"]
    text = _prompt_text()
    assert "`review`" in text and "merge-base" in text and "`notes`" in text
    assert "branch's upstream" in text


def test_pp_review_prompt_takes_an_explicit_base():
    text = _prompt_text(base="release/1.x")
    assert "The base is `release/1.x`." in text
    assert "upstream" not in text


def test_the_removed_allow_defer_argument_is_rejected():
    with pytest.raises(ToolError, match="allow_defer"):
        anyio.run(server.call_tool, "ask", {"question": "q", "allow_defer": True})

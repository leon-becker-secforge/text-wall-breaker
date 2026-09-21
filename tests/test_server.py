from __future__ import annotations

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pretty_prompt.server import accent_config_problem, environment_problem, server


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


def test_accent_config_is_accepted_when_unset(monkeypatch):
    monkeypatch.delenv("PRETTY_PROMPT_ACCENT", raising=False)
    assert accent_config_problem() is None


def test_accent_config_is_accepted_when_a_valid_hex_colour(monkeypatch):
    monkeypatch.setenv("PRETTY_PROMPT_ACCENT", "#2563eb")
    assert accent_config_problem() is None


def test_accent_config_is_refused_when_not_a_hex_colour(monkeypatch):
    monkeypatch.setenv("PRETTY_PROMPT_ACCENT", "blue")
    problem = accent_config_problem() or ""
    assert "PRETTY_PROMPT_ACCENT" in problem
    assert "blue" in problem


def test_ask_and_show_are_the_only_registered_tools():
    tools = anyio.run(server.list_tools)
    assert [tool.name for tool in tools] == ["ask", "show"]


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
        "timeout_seconds",
        "questions",
    }
    option = schema["$defs"]["Option"]["properties"]
    assert set(option) == {"id", "label", "body", "recommended"}
    question = schema["$defs"]["Question"]["properties"]
    assert set(question) == {"question", "options", "multi_select", "allow_free_text"}


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

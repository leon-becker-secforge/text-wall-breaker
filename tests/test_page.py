from __future__ import annotations

import pytest

from pretty_prompt.page import InvalidAccent, build_page, parse_accent


@pytest.mark.parametrize("value", ["#1f7a4c", "1f7a4c", "#1F7A4C", " #1f7a4c "])
def test_parse_accent_accepts_hex_with_or_without_hash(value):
    assert parse_accent(value) == (0x1F, 0x7A, 0x4C)


@pytest.mark.parametrize("value", ["", "green", "#1f7a4", "#1f7a4c1", "#gggggg"])
def test_parse_accent_rejects_anything_else(value):
    with pytest.raises(InvalidAccent, match=repr(value) if value else "hex colour"):
        parse_accent(value)


def _page(**overrides):
    overrides.setdefault("options", [])
    overrides.setdefault("timeout_seconds", 300.0)
    return build_page(
        question="q",
        multi_select=False,
        allow_free_text=True,
        submit_url="http://127.0.0.1:1/submit",
        **overrides,
    )


def _body(html: str) -> str:
    """The page's `<body>`, so a match can't come from a comment in the `<style>` block."""
    return html.split("<body>", 1)[1]


def test_default_page_uses_the_built_in_green_only():
    html = _page()
    assert "#1f7a4c" in html
    assert "#3ddc84" in html


def test_custom_accent_overrides_the_built_in_green():
    html = _page(accent="#2563eb")
    assert "#2563eb" in html
    # The override is appended after the built-in palette, not instead of it, so the
    # built-in values are still present in the stylesheet — only the cascade changes.
    assert "#1f7a4c" in html


def test_timeout_seconds_is_embedded_for_the_heartbeat():
    html = _page(timeout_seconds=42.0)
    assert "var TIMEOUT_SECONDS = 42.0;" in html


def test_invalid_accent_raises_from_build_page():
    with pytest.raises(InvalidAccent):
        _page(accent="not-a-colour")


def _option(**kwargs):
    return {"id": "a", "label": "A", "body": None, "recommended": False, **kwargs}


_DRAWER_OPEN_TAG = '<details class="pp-option-drawer" open>'
_DRAWER_CLOSED_TAG = '<details class="pp-option-drawer">'


def test_option_with_no_body_is_a_plain_label():
    body = _body(_page(options=[_option(body=None)]))
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body
    assert '<div class="pp-option-body">' not in body
    assert '<label class="pp-option-select">' in body


def test_short_body_is_shown_inline_without_a_drawer():
    body = _body(_page(options=[_option(body="A short body.")]))
    assert '<div class="pp-option-body">' in body
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body
    assert "pp-option-chevron" not in body


def test_long_body_collapses_behind_a_drawer():
    body = _body(_page(options=[_option(body="x " * 250)]))  # well past _LONG_BODY_CHARS
    assert _DRAWER_CLOSED_TAG in body
    assert '<summary class="pp-option-select">' in body
    assert "pp-option-chevron" in body


def test_an_image_collapses_a_body_regardless_of_length():
    body = _body(_page(options=[_option(body="![x](https://example.com/x.png)")]))
    assert _DRAWER_CLOSED_TAG in body


def test_a_code_block_collapses_a_body_regardless_of_length():
    body = _body(_page(options=[_option(body="```\ncode\n```")]))
    assert _DRAWER_CLOSED_TAG in body


def test_recommended_long_body_starts_open():
    body = _body(_page(options=[_option(body="x " * 250, recommended=True)]))
    assert _DRAWER_OPEN_TAG in body


def test_recommended_has_no_effect_on_a_short_body():
    # No <details> exists to open — recommended is silently a no-op rather than an error.
    body = _body(_page(options=[_option(body="short", recommended=True)]))
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body


def test_a_drawer_input_has_an_accessible_name():
    # A long body wraps the input in <summary>, not <label> — <summary> confers no
    # implicit accessible name, so this has to come from aria-label instead.
    body = _body(_page(options=[_option(label="Postgres", body="x " * 250)]))
    assert 'aria-label="Postgres"' in body

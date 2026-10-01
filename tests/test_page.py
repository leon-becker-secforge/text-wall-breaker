from __future__ import annotations

import re

import pytest

from pretty_prompt.page import (
    InvalidAccent,
    build_multi_page,
    build_page,
    parse_accent,
    read_saved_theme,
    save_theme,
)


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


_DRAWER_OPEN_TAG = '<details class="pp-option-drawer" data-option-id="a" open>'
_DRAWER_CLOSED_TAG = '<details class="pp-option-drawer" data-option-id="a">'


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


def test_recommended_short_body_has_no_drawer_to_open():
    body = _body(_page(options=[_option(body="short", recommended=True)]))
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body


_RECOMMENDED_TAG = '<span class="pp-option-recommended">Recommended</span>'


@pytest.mark.parametrize("body", [None, "short", "x " * 250], ids=["no-body", "short", "long"])
def test_recommended_option_carries_a_badge_after_its_label(body):
    html = _body(_page(options=[_option(label="Postgres", body=body, recommended=True)]))
    assert f'<span class="pp-option-label">Postgres</span>{_RECOMMENDED_TAG}' in html


def test_option_not_recommended_has_no_badge():
    assert "pp-option-recommended" not in _body(_page(options=[_option()]))


def test_a_drawer_input_has_an_accessible_name():
    # A long body wraps the input in <summary>, not <label> — <summary> confers no
    # implicit accessible name, so this has to come from aria-label instead.
    body = _body(_page(options=[_option(label="Postgres", body="x " * 250)]))
    assert 'aria-label="Postgres"' in body


_CAVEAT_DIV = '<div class="pp-option-caveat">'


def test_no_caveat_renders_no_caveat_element():
    assert _CAVEAT_DIV not in _body(_page(options=[_option(body="x " * 250)]))


def test_caveat_on_a_drawer_sits_inside_the_summary():
    # Inside <summary>, not beside the body: a closed <details> shows only its summary,
    # and the caveat's whole point is being visible without opening the drawer.
    body = _body(_page(options=[_option(body="x " * 250, caveat="Needs Q3.")]))
    summary = body.split("<summary", 1)[1].split("</summary>", 1)[0]
    assert f"{_CAVEAT_DIV}Needs Q3.</div>" in summary


def test_caveat_on_an_option_without_a_body():
    body = _body(_page(options=[_option(caveat="Needs Q3.")]))
    assert f"{_CAVEAT_DIV}Needs Q3.</div>" in body


def test_caveat_comes_before_a_short_inline_body():
    body = _body(_page(options=[_option(body="Short body.", caveat="Needs Q3.")]))
    assert body.index(_CAVEAT_DIV) < body.index('<div class="pp-option-body">')


def test_caveat_renders_inline_markdown_only():
    body = _body(_page(options=[_option(caveat="Costs one `uv` process, **each**.")]))
    assert f"{_CAVEAT_DIV}Costs one <code>uv</code> process, <strong>each</strong>.</div>" in body
    # Block syntax is not rendered as a block: no <ul>, no <p> inside the caveat.
    body = _body(_page(options=[_option(caveat="- not a list")]))
    assert f"{_CAVEAT_DIV}- not a list</div>" in body


_DEFER_BUTTON = '<button type="button" class="pp-defer" aria-pressed="false">Not yet</button>'
_DEFER_BANNER = '<div class="pp-defer-banner" hidden>'


def test_no_defer_control_by_default():
    body = _body(_page())
    assert 'class="pp-defer"' not in body
    assert _DEFER_BANNER not in body


def test_allow_defer_adds_the_pill_and_a_hidden_banner():
    body = _body(_page(allow_defer=True))
    assert _DEFER_BUTTON in body
    assert _DEFER_BANNER in body


def _multi_page(**overrides):
    overrides.setdefault(
        "questions",
        [
            {"question": "first", "options": [_option(id="a", label="A")]},
            {"question": "second", "options": [_option(id="b", label="B")]},
        ],
    )
    overrides.setdefault("timeout_seconds", 300.0)
    return build_multi_page(submit_url="http://127.0.0.1:1/submit", **overrides)


def test_multi_page_renders_every_question():
    # Every step's markup is in the page even though only one is shown at a time — the
    # wizard toggles visibility with CSS, it doesn't omit the other steps.
    body = _body(_multi_page())
    assert "first" in body
    assert "second" in body
    assert 'data-option-id="a"' in body
    assert 'data-option-id="b"' in body


def test_multi_page_shows_a_shared_progress_label_and_bar():
    body = _body(_multi_page())
    assert 'id="pp-progress-label">Question 1 of 2<' in body
    assert "width: 50%" in body


def test_multi_page_only_the_first_step_starts_active():
    body = _body(_multi_page())
    assert 'class="pp-step pp-step-active" data-step-index="0"' in body
    assert 'class="pp-step" data-step-index="1"' in body


def test_multi_page_single_question_starts_with_send_as_the_next_label():
    body = _body(_multi_page(questions=[{"question": "only", "options": [_option()]}]))
    assert 'id="pp-next" disabled>Send answers<' in body


def test_multi_page_scopes_option_names_and_steps_per_question():
    body = _body(_multi_page())
    assert 'name="pp-option-step-0"' in body
    assert 'name="pp-option-step-1"' in body
    assert 'data-step="0"' in body
    assert 'data-step="1"' in body


def test_multi_page_free_text_is_scoped_per_question():
    body = _body(
        _multi_page(
            questions=[
                {"question": "first", "options": [], "allow_free_text": True},
                {
                    "question": "second",
                    "options": [_option(id="b", label="B")],
                    "allow_free_text": False,
                },
            ]
        )
    )
    assert 'id="pp-free-text-0"' in body
    assert 'id="pp-free-text-1"' not in body


def test_multi_page_embeds_the_step_count():
    html = _multi_page()
    assert "var STEP_COUNT = 2;" in html


def test_multi_page_default_accent_matches_single_page():
    assert "#1f7a4c" in _multi_page()


def test_multi_page_custom_accent_overrides_the_built_in_green():
    html = _multi_page(accent="#2563eb")
    assert "#2563eb" in html
    assert "#1f7a4c" in html


def test_multi_page_invalid_accent_raises():
    with pytest.raises(InvalidAccent):
        _multi_page(accent="not-a-colour")


@pytest.fixture
def theme_state_home(monkeypatch, tmp_path):
    """Point the saved-theme file at a throwaway directory, never the real user state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def test_read_saved_theme_is_none_when_nothing_was_saved(theme_state_home):
    assert read_saved_theme() is None


def test_save_then_read_round_trips(theme_state_home):
    save_theme("dark")
    assert read_saved_theme() == "dark"
    save_theme("light")
    assert read_saved_theme() == "light"


def test_save_theme_ignores_an_invalid_value(theme_state_home):
    save_theme("nope")
    assert read_saved_theme() is None


def test_read_saved_theme_ignores_a_corrupted_file(theme_state_home):
    path = theme_state_home / "pretty-prompt" / "theme"
    path.parent.mkdir(parents=True)
    path.write_text("purple", encoding="utf-8")
    assert read_saved_theme() is None


def test_page_bakes_in_the_saved_theme_attribute():
    assert '<html lang="en" data-theme="dark">' in _page(theme="dark")
    assert '<html lang="en" data-theme="light">' in _page(theme="light")


def test_page_has_no_theme_attribute_when_unsaved():
    html = _page(theme=None)
    assert '<html lang="en">' in html
    assert '<html lang="en" data-theme' not in html


def test_multi_page_bakes_in_the_saved_theme_attribute():
    assert '<html lang="en" data-theme="dark">' in _multi_page(theme="dark")


def test_page_title_marks_answered_and_cancelled():
    html = _page()
    assert "document.title = '\\u2705 ' + BASE_TITLE;" in html
    assert "document.title = '\\u2716 ' + BASE_TITLE;" in html


def test_multi_page_title_marks_answered_and_cancelled():
    html = _multi_page()
    assert "document.title = '\\u2705 ' + BASE_TITLE;" in html
    assert "document.title = '\\u2716 ' + BASE_TITLE;" in html


def test_every_page_carries_a_lightbox_and_zoom_listener():
    assert 'id="pp-lightbox"' in _page()
    assert 'id="pp-lightbox"' in _multi_page()


def test_one_image_option_does_not_trigger_gallery_mode():
    body = _body(_page(options=[_option(id="a", body="![x](https://example.com/a.png)")]))
    assert "pp-options-gallery" not in body
    # A single image is still shown behind a drawer, same as any other long body.
    assert '<details class="pp-option-drawer" data-option-id="a">' in body


def test_two_image_options_trigger_gallery_mode():
    body = _body(
        _page(
            options=[
                _option(id="a", body="![x](https://example.com/a.png)"),
                _option(id="b", body="![y](https://example.com/b.png)"),
            ]
        )
    )
    assert "pp-options-gallery" in body
    # Gallery mode never drawers an image behind a click. Scoped to the markup, not the
    # script — the script's own drawer-querying selector is a `pp-option-drawer` literal
    # regardless of whether the page has any.
    markup = body.split("<script>", 1)[0]
    assert "pp-option-drawer" not in markup


def test_gallery_widens_the_page():
    html = _page(
        options=[
            _option(id="a", body="![x](https://example.com/a.png)"),
            _option(id="b", body="![y](https://example.com/b.png)"),
        ]
    )
    assert '<main class="pp-wide">' in html


def test_non_gallery_page_has_a_plain_main_tag():
    body = _body(_page(options=[_option()]))
    assert "<main>" in body
    assert "pp-wide" not in body


def test_multi_page_widens_when_any_step_is_a_gallery():
    html = _multi_page(
        questions=[
            {"question": "first", "options": [_option()]},
            {
                "question": "second",
                "options": [
                    _option(id="a", body="![x](https://example.com/a.png)"),
                    _option(id="b", body="![y](https://example.com/b.png)"),
                ],
            },
        ]
    )
    assert '<main class="pp-wide">' in html


def test_lightbox_display_rule_is_guarded_against_the_hidden_attribute():
    # Regression: `.pp-lightbox { display: flex; ... }` alone beats the browser's own
    # `[hidden] { display: none }` (author origin outranks user-agent origin regardless
    # of specificity), which left the full-page overlay visible at all times — the whole
    # page reading as uniformly, wrongly dark, not just while an image was zoomed.
    for html in (_page(), _multi_page()):
        assert ".pp-lightbox[hidden] { display: none; }" in html


def test_multi_page_offers_defer_only_on_the_steps_that_allow_it():
    body = _body(
        _multi_page(
            questions=[
                {"question": "first", "options": [_option(id="a")]},
                {"question": "second", "options": [_option(id="b")], "allow_defer": True},
            ]
        )
    )
    first, second = body.split('data-step-index="1"', 1)
    assert 'class="pp-defer"' not in first
    assert _DEFER_BUTTON in second


def test_multi_page_has_no_header_by_default():
    assert 'class="pp-header' not in _body(_multi_page())


def test_a_short_header_is_shown_flat_above_the_progress_bar():
    body = _body(_multi_page(header="Settled: **SQLite**."))
    header = '<div class="pp-header"><p>Settled: <strong>SQLite</strong>.</p></div>'
    assert header in body
    assert body.index(header) < body.index('class="pp-progress"')


def test_a_long_header_collapses_behind_a_context_toggle():
    body = _body(_multi_page(header="x " * 250))
    assert '<details class="pp-header">' in body
    assert "<summary>Context</summary>" in body


def test_a_long_header_that_is_one_list_counts_its_items():
    items = "\n".join(f"- decision number {i} " + "x " * 30 for i in range(7))
    body = _body(_multi_page(header=items))
    assert '<summary>Context <span class="pp-header-count">7 items</span></summary>' in body


def test_a_selected_option_starts_checked_and_free_text_prefilled():
    page = _page(
        options=[{"id": "a", "label": "A", "selected": True}, {"id": "b", "label": "B"}],
        free_text_prefill="</textarea><b>",
    )
    assert len(re.findall(r"<input[^>]* checked ", page)) == 1
    assert "&lt;/textarea&gt;&lt;b&gt;</textarea>" in page


def test_multi_page_prefills_per_step():
    page = build_multi_page(
        questions=[
            {"question": "q", "options": [{"id": "a", "label": "A", "selected": True}]},
            {"question": "q2", "options": [], "free_text": "hello"},
        ],
        submit_url="http://127.0.0.1:1/submit",
        timeout_seconds=300.0,
    )
    assert len(re.findall(r"<input[^>]* checked ", page)) == 1
    assert ">\nhello</textarea>" in page

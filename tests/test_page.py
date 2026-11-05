from __future__ import annotations

import difflib
import json
import re

import pytest

from pretty_prompt.page import (
    InvalidAccent,
    build_multi_page,
    build_page,
    build_review_page,
    build_show_page,
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


def test_heartbeat_runs_whether_or_not_the_tab_is_visible():
    # A hidden tab is still in use; gating the heartbeat on visibility would make the
    # server abandon the call while the user is in another window.
    review = build_review_page(
        pages=[{"path": "a.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}],
        submit_url="http://127.0.0.1:1/submit",
    )
    for html in (
        _page(),
        _multi_page(),
        build_show_page(content="# R", submit_url="http://x"),
        review,
    ):
        assert "var HEARTBEAT_MS = 15000;" in html
        assert "TIMEOUT_SECONDS" not in html
        assert "visibilityState !== 'visible') return" not in html
        assert "visibilityState === 'visible') send(" not in html


def test_review_heartbeat_reports_the_comments_so_far():
    html = build_review_page(
        pages=[{"path": "a.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}],
        submit_url="http://127.0.0.1:1/submit",
    )
    assert "send('progress', collect())" in html


def test_review_placeholders_in_the_diff_text_stay_text():
    diff = "@@ -1 +1 @@\n-__DECK_SCRIPT__\n+__LIVE_SCRIPT__ __SUBMIT_URL__\n"
    html = build_review_page(
        pages=[{"path": "page.py", "diff": diff}],
        submit_url="http://127.0.0.1:1/submit",
        status_url="http://127.0.0.1:1/status",
        live="on",
    )
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert sum("window.ppLive = {" in s for s in scripts) == 1
    assert "__LIVE_SCRIPT__" in html


def test_live_off_leaves_out_the_live_script():
    html = build_review_page(
        pages=[{"path": "a.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}],
        submit_url="http://127.0.0.1:1/submit",
    )
    assert "window.ppLive = " not in html
    assert 'liveMode: "off"' in html


def test_review_gives_every_page_its_own_note_box():
    diff = "@@ -1 +1 @@\n-a\n+b\n"
    html = build_review_page(
        pages=[{"path": "a.py", "diff": diff}, {"path": 'we"ird<.py', "diff": diff}],
        submit_url="http://127.0.0.1:1/submit",
    )
    assert len(re.findall(r'<textarea class="pp-page-note"', html)) == 2
    assert 'id="pp-page-note-0"' in html
    assert 'id="pp-page-note-1"' in html
    assert 'data-path="we&quot;ird&lt;.py"' in html
    assert "page_notes: collectPageNotes()" in html


_REVIEW_DIFF = "@@ -1 +1 @@\n-a\n+b\n"


def _review(*paths):
    return build_review_page(
        pages=[{"path": path, "diff": _REVIEW_DIFF} for path in paths],
        submit_url="http://127.0.0.1:1/submit",
    )


def test_review_lists_every_file_with_its_directory_and_marks_the_first_current():
    html = _review("src/app/config.py", "README.md")
    assert '<body class="pp-has-files">' in html
    assert re.findall(r'<span class="pp-file-name">([^<]*)</span>', html) == [
        "config.py",
        "README.md",
    ]
    assert re.findall(r'<span class="pp-file-dir">([^<]*)</span>', html) == ["src/app/", "./"]
    assert html.count("pp-file is-current") == 1
    assert 'title="src/app/config.py"' in html


def test_review_file_list_escapes_paths():
    html = _review('a/<b>"c".py', "x.py")
    assert 'title="a/&lt;b&gt;&quot;c&quot;.py"' in html
    assert '<span class="pp-file-name">&lt;b&gt;&quot;c&quot;.py</span>' in html


def test_a_long_file_list_gets_a_filter_and_a_short_one_does_not():
    assert 'id="pp-files-filter"' in _review(*[f"d/f{n}.py" for n in range(8)])
    assert 'id="pp-files-marked"' in _review(*[f"d/f{n}.py" for n in range(8)])
    assert 'id="pp-files-filter"' not in _review(*[f"d/f{n}.py" for n in range(7)])


def test_review_with_one_file_has_no_file_list():
    html = _review("only.py")
    assert 'id="pp-files"' not in html
    assert 'id="pp-filebar"' not in html
    assert "pp-has-files" not in html.split("<body", 1)[1].split(">", 1)[0]


def test_review_file_list_counts_come_from_the_page_not_a_second_copy():
    files_script = _review("a.py", "b.py").split("var slides", 1)[1]
    assert "querySelectorAll('.pp-comment')" in files_script
    assert ".pp-page-note" in files_script
    assert "'pp-comments'" in files_script


def test_review_rows_are_selected_not_given_a_comment_box_each():
    html = _review("a.py")
    assert 'class="pp-review-row-wrap"' in html
    assert "pp-review-comment-box" not in html
    assert html.count('id="pp-dock"') == 1
    assert 'id="pp-dock-text"' in html


def test_review_collects_ranges_with_an_end_and_a_quote():
    html = _review("a.py")
    assert "out.end_side = end.side; out.end_line = end.line;" in html
    assert "if (c.quote) out.quote = c.quote;" in html


_OLD = [f"line {k}" for k in range(1, 101)]
_NEW = list(_OLD)
_NEW[4] = "line 5 changed"
_NEW[44] = "line 45 changed"
_NEW[90] = "line 91 changed"


def _expandable(lines=_NEW, diff_new=_NEW):
    diff = "".join(
        difflib.unified_diff(
            [x + "\n" for x in _OLD], [x + "\n" for x in diff_new], "a/f", "b/f", n=3
        )
    )
    return build_review_page(
        pages=[{"path": "f.py", "diff": diff, "lines": lines}],
        submit_url="http://127.0.0.1:1/submit",
    )


def _gaps(html):
    return re.findall(
        r'<div class="pp-review-gap" data-position="(\w+)" data-first="(\d+)" '
        r'data-last="(\d+)" data-delta="(-?\d+)">',
        html,
    )


def test_review_without_the_file_has_no_gap_bars_and_no_lines_payload():
    html = _review("a.py")
    assert 'class="pp-review-gap"' not in html
    assert '<script type="application/json"' not in html


def test_review_shows_a_bar_for_every_hidden_stretch():
    assert _gaps(_expandable()) == [
        ("top", "1", "1", "0"),
        ("between", "9", "41", "0"),
        ("between", "49", "87", "0"),
        ("end", "95", "100", "0"),
    ]


def test_a_gap_bar_sits_directly_before_the_hunk_it_precedes():
    html = _expandable()
    assert re.search(
        r'data-last="1" data-delta="0">.*?</div>\s*<div class="pp-review-row hunk">', html
    )
    assert html.index('data-position="between" data-first="9"') < html.index("@@ -42")


def test_only_the_steps_that_make_sense_are_offered_and_short_gaps_have_none_visible():
    html = _expandable()
    top = html.split('data-position="top"', 1)[1].split("</div>", 1)[0]
    end = html.split('data-position="end"', 1)[1].split("</div>", 1)[0]
    between = html.split('data-position="between" data-first="9"', 1)[1].split("</div>", 1)[0]
    assert 'data-step="up"' in top
    assert 'data-step="down"' not in top
    assert 'data-step="down"' in end
    assert 'data-step="up"' not in end
    assert 'data-step="down" hidden' in end  # 6 lines: the middle shows them all
    assert 'data-step="down"' in between
    assert 'data-step="down" hidden' not in between  # 33 lines


def test_the_hidden_lines_travel_in_one_json_element_in_gap_order():
    html = _expandable()
    [payload] = re.findall(
        r'<script type="application/json" class="pp-review-lines">(.*?)</script>', html
    )
    gaps = json.loads(payload)
    assert gaps[0] == ["line 1"]
    assert gaps[1] == _NEW[8:41]
    assert len(gaps) == 4
    assert gaps[3] == _NEW[94:100]


def test_a_file_containing_a_closing_script_tag_cannot_end_the_lines_element():
    nasty = [*_NEW]
    nasty[20] = "</script><script>alert(1)</script>"
    html = _expandable(lines=nasty, diff_new=_NEW)
    # the diff's own rows are escaped by html.escape; the JSON payload by \u003c
    assert "<script>alert(1)" not in html
    assert "\\u003c/script>" in html


def test_review_script_tracks_rows_as_elements_and_blocks_a_comment_over_hidden_lines():
    html = _expandable()
    assert "function spansHiddenLines" in html
    assert "if (o.first === o.last) return false;" in html
    assert "var GAP_STEP = 10;" in html
    assert "first: sel.first, last: sel.last" in html


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
    assert 'class="pp-option-details"' not in body


def test_long_body_collapses_behind_a_drawer():
    body = _body(_page(options=[_option(body="x " * 250)]))  # well past _LONG_BODY_CHARS
    assert _DRAWER_CLOSED_TAG in body
    assert '<summary class="pp-option-select">' in body
    assert 'class="pp-option-details"' in body


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


def test_option_label_renders_inline_markdown():
    body = _body(_page(options=[_option(label="plain **bold** and `code`")]))
    assert (
        '<span class="pp-option-label">plain <strong>bold</strong> and <code>code</code></span>'
        in body
    )


def test_option_label_aria_name_stays_plain_text():
    # The accessible name is read as text, not markup, so it must not carry the <strong>
    # tag the visible label now gets.
    body = _body(_page(options=[_option(label="plain **bold** title")]))
    assert 'aria-label="plain **bold** title"' in body


def test_option_label_leading_quote_marker_stays_literal():
    # Block syntax (a leading `>`) has nowhere to go in a one-line title, same reasoning
    # as a caveat's inline-only rendering — it stays visible text, not a <blockquote>.
    body = _body(_page(options=[_option(label="> quoted title")]))
    assert "<blockquote>" not in body
    assert '<span class="pp-option-label">&gt; quoted title</span>' in body


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
    return build_multi_page(submit_url="http://127.0.0.1:1/submit", **overrides)


def test_multi_page_renders_every_question():
    # Every step's markup is in the page even though only one is shown at a time — the
    # wizard toggles visibility with CSS, it doesn't omit the other steps.
    body = _body(_multi_page())
    assert "first" in body
    assert "second" in body
    assert 'data-option-id="a"' in body
    assert 'data-option-id="b"' in body


def test_multi_page_shows_a_shared_progress_label_and_one_segment_per_question():
    body = _body(_multi_page())
    assert 'id="pp-progress-label">Question 1 of 2<' in body
    # Starts on question 1: its segment is current, and none is enabled (going back needs
    # a question to go back to).
    assert body.count('<button type="button" class="pp-seg') == 2
    assert 'class="pp-seg pp-seg-cur" data-step="0"' in body
    assert 'class="pp-seg" data-step="1"' in body
    assert body.count('aria-label="Question ') == 2
    assert 'data-step="0" aria-label="Question 1" disabled' in body


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
    )
    assert len(re.findall(r"<input[^>]* checked ", page)) == 1
    assert ">\nhello</textarea>" in page


def test_long_question_marks_its_block_and_bolds_only_the_lead():
    html = build_page(
        question="Lead sentence. " + "Detail follows here. " * 20,
        options=[{"id": "a", "label": "A"}],
        multi_select=False,
        allow_free_text=False,
        submit_url="http://127.0.0.1:1/s",
    )
    assert 'class="pp-question pp-long"' in html
    assert "<strong>Lead sentence.</strong>" in html


def test_a_picked_radio_shows_it_can_be_cleared_on_hover_on_both_pages():
    # Keyed on :checked and :not(:disabled), so it never shows on a checkbox, an empty
    # question, or a page frozen after Send.
    selector = '.pp-option:hover input[type="radio"]:checked:not(:disabled)'
    for html in (_page(), _multi_page()):
        assert selector in html
        assert 'content: "click again to clear"' not in html


def test_both_pages_offer_stop_here_and_send_it_as_an_interrupt():
    for html in (_page(), _multi_page()):
        assert 'id="pp-stop"' in html
        assert 'id="pp-stop-send" hidden disabled' in html
        assert "send('interrupt', data)" in html
        assert "__STOP__" not in html


def test_every_page_type_shares_one_action_row_stylesheet():
    from pretty_prompt.page import build_review_page, build_show_page

    u = "http://127.0.0.1:1/submit"
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    pages = (
        _page(),
        _multi_page(),
        build_show_page(content="# R", submit_url=u),
        build_review_page(pages=[{"path": "a.py", "diff": diff}], submit_url=u),
    )
    for html in pages:
        assert html.count(".pp-actions .pp-actions-end") == 1
        # A colour rule keyed on an id alone outranks `:disabled` and keeps a disabled
        # primary solid; every one must be scoped to `:not(:disabled)`.
        assert "#pp-submit, #pp-next" not in html
        assert ".pp-actions button:disabled" in html
        # Beside the primary, inside its group: above or between the groups, showing or
        # hiding it moved the buttons.
        assert re.search(r'pp-actions-end">\s*<span id="pp-status"', html)


def test_wizard_segments_only_navigate_backwards():
    html = _multi_page()
    # An enabled segment is one before the current question; the script must never enable
    # the current or a later one, or a click could skip Next's answer check.
    assert "seg.disabled = frozen || segmentsLocked || i >= current;" in html
    assert "if (frozen || segmentsLocked || i >= current) return;" in html


def test_progress_fill_is_a_block_so_it_has_a_size():
    from pretty_prompt.page import build_review_page, build_show_page

    u = "http://127.0.0.1:1/submit"
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    pages = (
        build_show_page(slides=["# 1", "# 2"], submit_url=u),
        build_review_page(pages=[{"path": "a.py", "diff": diff}], submit_url=u),
    )
    for html in pages:
        # An inline span ignores width and height, so the fill never showed.
        assert ".pp-progress-fill { display: block;" in html


def test_agent_notes_render_open_under_their_last_row_with_a_chip_on_the_first():
    diff = "@@ -1,2 +1,3 @@\n a\n+b\n+c\n"
    notes = [{"side": "new", "line": 2, "end_side": "new", "end_line": 3, "text": "Why **x**"}]
    html = build_review_page(
        pages=[{"path": "a.py", "diff": diff, "notes": notes}], submit_url="http://x/submit"
    )
    body = html.split('<div class="pp-review-diff"', 1)[1]
    chip = body.index('class="pp-note-mark is-open" data-note="0"')
    note = body.index('<div class="pp-note is-open" data-note="0">')
    assert body.index('data-line="2"') < chip < body.index('data-line="3"') < note
    assert "Agent &middot; new 2\N{EN DASH}3" in body
    assert "<strong>x</strong>" in body[note:]
    assert "1 note from the agent" in html


def test_the_notes_bar_is_a_labelled_two_segment_switch():
    diff = "@@ -1,1 +1,2 @@\n a\n+b\n"
    notes = [{"side": "new", "line": 2, "text": "why"}]
    html = build_review_page(
        pages=[{"path": "a.md", "diff": diff, "notes": notes, "rendered": "<p>x</p>"}],
        submit_url="http://x/submit",
    )
    assert '<div class="pp-notes-bar" data-state="shown">' in html
    assert (
        '<span class="pp-switch"><button type="button" data-all="0">Hide all</button>'
        '<button type="button" data-all="1">Show all</button></span>'
    ) in html


def _head(header, **page):
    diff = "@@ -1,1 +1,2 @@\n a\n+b\n"
    pages = [{"path": "src/pkg/a.py", "diff": diff, **page}]
    return build_review_page(pages=pages, header=header, submit_url="http://x/submit")


def test_the_header_is_a_slim_row_with_the_rest_folded_under_details():
    html = _head("# Retry rework\n\nWhy it changed.\n\n- one\n- two")
    assert '<h1 class="pp-head-title">Retry rework</h1>' in html
    assert '<span class="pp-chip"><b>1</b> file</span>' in html
    assert 'id="pp-chip-comments"><b>0</b> comments</span>' in html
    assert '<details class="pp-head-details"><summary>Details</summary>' in html
    assert "Why it changed." in html.split("<summary>Details</summary>", 1)[1]


def test_a_header_without_a_heading_is_not_hidden_and_none_is_just_the_row():
    open_html = _head("Just a paragraph.")
    assert '<span class="pp-head-title">Review</span>' in open_html
    assert '<details class="pp-head-details" open>' in open_html
    bare = _head(None).split('<header class="pp-head">', 1)[1].split("</header>", 1)[0]
    assert '<span class="pp-head-title">Review</span>' in bare
    assert "<details" not in bare
    assert "agent note" not in bare


def test_the_path_is_a_dim_folder_and_a_bold_file_and_the_blurb_is_wrapped():
    html = _head(None, blurb="The **core** change.")
    assert (
        '<div class="pp-review-path"><span class="pp-path-dir">src/pkg/</span>'
        '<span class="pp-path-file">a.py</span></div>'
    ) in html
    assert '<div class="pp-blurb"><p>The <strong>core</strong> change.</p></div>' in html


def test_switching_file_drops_an_open_selection_and_its_comment_box():
    pages: list[dict[str, object]] = [
        {"path": "a.py", "diff": "@@ -1,1 +1,2 @@\n a\n+b\n"},
        {"path": "b.py", "diff": "@@ -1,1 +1,2 @@\n a\n+b\n"},
    ]
    html = build_review_page(pages=pages, submit_url="http://x/submit")
    # the file list announces every switch and the review script clears the selection on it
    assert "document.dispatchEvent(new CustomEvent('pp-file-change'))" in html
    assert (
        "document.addEventListener('pp-file-change', function () { if (sel) clearSel(); });" in html
    )


def test_a_page_without_notes_has_no_note_markup():
    html = _review("a.py")
    assert '<div class="pp-notes-bar">' not in html
    assert '<button type="button" class="pp-note-mark' not in html

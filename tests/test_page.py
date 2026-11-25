from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from importlib import resources

import pytest

from text_wall_breaker import config
from text_wall_breaker import page as page_module
from text_wall_breaker.config import Config
from text_wall_breaker.page import (
    InvalidAccent,
    ReviewNote,
    ReviewPage,
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
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert sum("window.twbLive = {" in s for s in scripts) == 1
    assert "__LIVE_SCRIPT__" in html


def test_live_off_leaves_out_the_live_script():
    html = build_review_page(
        pages=[{"path": "a.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}],
        submit_url="http://127.0.0.1:1/submit",
    )
    assert "window.twbLive = " not in html
    assert 'liveMode: "off"' in html


def test_review_gives_every_page_its_own_note_box():
    diff = "@@ -1 +1 @@\n-a\n+b\n"
    html = build_review_page(
        pages=[{"path": "a.py", "diff": diff}, {"path": 'we"ird<.py', "diff": diff}],
        submit_url="http://127.0.0.1:1/submit",
    )
    assert len(re.findall(r'<textarea class="twb-page-note"', html)) == 2
    assert 'id="twb-page-note-0"' in html
    assert 'id="twb-page-note-1"' in html
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
    assert '<body class="twb-has-files">' in html
    assert re.findall(r'<span class="twb-file-name">([^<]*)</span>', html) == [
        "config.py",
        "README.md",
    ]
    assert re.findall(r'<span class="twb-file-dir">([^<]*)</span>', html) == ["src/app/", "./"]
    assert html.count("twb-file is-current") == 1
    assert 'title="src/app/config.py"' in html


def test_review_file_list_escapes_paths():
    html = _review('a/<b>"c".py', "x.py")
    assert 'title="a/&lt;b&gt;&quot;c&quot;.py"' in html
    assert '<span class="twb-file-name">&lt;b&gt;&quot;c&quot;.py</span>' in html


def test_a_long_file_list_gets_a_filter_and_a_short_one_does_not():
    assert 'id="twb-files-filter"' in _review(*[f"d/f{n}.py" for n in range(8)])
    assert 'id="twb-files-marked"' in _review(*[f"d/f{n}.py" for n in range(8)])
    assert 'id="twb-files-filter"' not in _review(*[f"d/f{n}.py" for n in range(7)])


def test_review_with_one_file_has_no_file_list():
    html = _review("only.py")
    assert 'id="twb-files"' not in html
    assert 'id="twb-filebar"' not in html
    assert "twb-has-files" not in html.split("<body", 1)[1].split(">", 1)[0]


def test_review_file_list_counts_come_from_the_page_not_a_second_copy():
    files_script = _review("a.py", "b.py").split("var slides", 1)[1]
    assert "querySelectorAll('.twb-comment')" in files_script
    assert ".twb-page-note" in files_script
    assert "'twb-comments'" in files_script


def test_review_rows_are_selected_not_given_a_comment_box_each():
    html = _review("a.py")
    assert 'class="twb-review-row-wrap"' in html
    assert "twb-review-comment-box" not in html
    assert html.count('id="twb-dock"') == 1
    assert 'id="twb-dock-text"' in html


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
        r'<div class="twb-review-gap" data-position="(\w+)" data-first="(\d+)" '
        r'data-last="(\d+)" data-delta="(-?\d+)">',
        html,
    )


def test_review_without_the_file_has_no_gap_bars_and_no_lines_payload():
    html = _review("a.py")
    assert 'class="twb-review-gap"' not in html
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
        r'data-last="1" data-delta="0">.*?</div>\s*<div class="twb-review-row hunk">', html
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
        r'<script type="application/json" class="twb-review-lines">(.*?)</script>', html
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


_DRAWER_OPEN_TAG = '<details class="twb-option-drawer" data-option-id="a" open>'
_DRAWER_CLOSED_TAG = '<details class="twb-option-drawer" data-option-id="a">'


def test_option_with_no_body_is_a_plain_label():
    body = _body(_page(options=[_option(body=None)]))
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body
    assert '<div class="twb-option-body">' not in body
    assert '<label class="twb-option-select">' in body


def test_short_body_is_shown_inline_without_a_drawer():
    body = _body(_page(options=[_option(body="A short body.")]))
    assert '<div class="twb-option-body">' in body
    assert _DRAWER_CLOSED_TAG not in body
    assert _DRAWER_OPEN_TAG not in body
    assert 'class="twb-option-details"' not in body


def test_long_body_collapses_behind_a_drawer():
    body = _body(_page(options=[_option(body="x " * 250)]))  # well past _LONG_BODY_CHARS
    assert _DRAWER_CLOSED_TAG in body
    assert '<summary class="twb-option-select">' in body
    assert 'class="twb-option-details"' in body


def test_an_image_collapses_a_body_regardless_of_length():
    body = _body(_page(options=[_option(body="![x](file:///tmp/x.png)")]))
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


_RECOMMENDED_TAG = '<span class="twb-option-recommended">Recommended</span>'


@pytest.mark.parametrize("body", [None, "short", "x " * 250], ids=["no-body", "short", "long"])
def test_recommended_option_carries_a_badge_ahead_of_its_label_text(body):
    # Ahead of the text, inside the label: the tag floats, and a float sits at the line
    # where it appears in the source.
    html = _body(_page(options=[_option(label="Postgres", body=body, recommended=True)]))
    assert (
        f'<span class="twb-option-label"><span class="twb-option-tags">{_RECOMMENDED_TAG}' in html
    )
    assert "</span>Postgres</span>" in html


def test_details_pill_floats_with_the_badge_inside_the_label():
    html = _body(_page(options=[_option(label="Postgres", body="x " * 250, recommended=True)]))
    assert (
        f'<span class="twb-option-tags">{_RECOMMENDED_TAG}<span class="twb-option-details"' in html
    )


def test_option_not_recommended_has_no_badge():
    assert "twb-option-recommended" not in _body(_page(options=[_option()]))


def test_a_drawer_input_has_an_accessible_name():
    # A long body wraps the input in <summary>, not <label> — <summary> confers no
    # implicit accessible name, so this has to come from aria-label instead.
    body = _body(_page(options=[_option(label="Postgres", body="x " * 250)]))
    assert 'aria-label="Postgres"' in body


_CAVEAT_DIV = '<div class="twb-option-caveat">'


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
        '<span class="twb-option-label">plain <strong>bold</strong> and <code>code</code></span>'
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
    assert '<span class="twb-option-label">&gt; quoted title</span>' in body


def test_a_rank_label_renders_inline_markdown_like_a_pick_label():
    html = build_multi_page(
        questions=[{"question": "q", "rank": True, "options": [{"id": "a", "label": "`x` **y**"}]}],
        submit_url=_SUBMIT,
    )
    assert (
        '<div class="twb-option-label" id="twb-rk0-0"><code>x</code> <strong>y</strong></div>'
        in html
    )


def test_a_rank_move_button_is_described_by_its_items_label():
    options = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    html = build_multi_page(
        questions=[
            {"question": "q", "rank": True, "options": options},
            {"question": "q", "rank": ["Now"], "options": options},
        ],
        submit_url=_SUBMIT,
    )
    # Row "b" of the ordered list (question 0) and chip "a" of the tiers (question 1).
    assert 'id="twb-rk0-1"' in html and 'aria-label="Move up" aria-describedby="twb-rk0-1"' in html
    assert 'id="twb-rk1-0"' in html and 'aria-describedby="twb-rk1-0"' in html


def test_a_short_question_leads_with_a_heading_and_a_long_one_does_not():
    def ask(question: str) -> str:
        return build_page(
            question=question,
            options=[],
            multi_select=False,
            allow_free_text=True,
            submit_url=_SUBMIT,
        )

    short = ask("**Pick one**")
    assert '<p role="heading" aria-level="1"><strong>Pick one</strong></p>' in short
    assert 'role="heading"' not in ask("Which of these should we do first, given the cost? " * 10)


def test_the_status_text_is_a_live_region_on_every_page():
    for html in (_page(), _multi_page(), build_show_page(content="x"), _every_page()["review"]):
        assert '<span id="twb-status" role="status">' in html


def test_caveat_comes_before_a_short_inline_body():
    body = _body(_page(options=[_option(body="Short body.", caveat="Needs Q3.")]))
    assert body.index(_CAVEAT_DIV) < body.index('<div class="twb-option-body">')


def test_caveat_renders_inline_markdown_only():
    body = _body(_page(options=[_option(caveat="Costs one `uv` process, **each**.")]))
    assert f"{_CAVEAT_DIV}Costs one <code>uv</code> process, <strong>each</strong>.</div>" in body
    # Block syntax is not rendered as a block: no <ul>, no <p> inside the caveat.
    body = _body(_page(options=[_option(caveat="- not a list")]))
    assert f"{_CAVEAT_DIV}- not a list</div>" in body


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
    assert 'id="twb-progress-label" role="progressbar"' in body
    assert (
        'aria-valuemax="2"' in body and 'aria-valuetext="Question 1 of 2">Question 1 of 2<' in body
    )
    # Starts on question 1: its segment is current, and none is enabled (going back needs
    # a question to go back to).
    assert body.count('<button type="button" class="twb-seg') == 2
    assert 'class="twb-seg twb-seg-cur" data-step="0"' in body
    assert 'class="twb-seg" data-step="1"' in body
    assert body.count('aria-label="Question ') == 2
    assert 'data-step="0" aria-label="Question 1" disabled' in body


def test_multi_page_only_the_first_step_starts_active():
    body = _body(_multi_page())
    assert 'class="twb-step twb-step-active" data-step-index="0"' in body
    assert 'class="twb-step" data-step-index="1"' in body


def test_multi_page_single_question_starts_with_send_as_the_next_label():
    body = _body(_multi_page(questions=[{"question": "only", "options": [_option()]}]))
    assert 'id="twb-next" disabled>Send answers<' in body


def test_multi_page_scopes_option_names_and_steps_per_question():
    body = _body(_multi_page())
    assert 'name="twb-option-step-0"' in body
    assert 'name="twb-option-step-1"' in body
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
    assert 'id="twb-free-text-0"' in body
    assert 'id="twb-free-text-1"' not in body


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
    path = theme_state_home / "text-wall-breaker" / "theme"
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
    assert 'id="twb-lightbox"' in _page()
    assert 'id="twb-lightbox"' in _multi_page()


def test_one_image_option_does_not_trigger_gallery_mode():
    body = _body(_page(options=[_option(id="a", body="![x](file:///tmp/a.png)")]))
    assert "twb-options-gallery" not in body
    # A single image is still shown behind a drawer, same as any other long body.
    assert '<details class="twb-option-drawer" data-option-id="a">' in body


def test_two_image_options_trigger_gallery_mode():
    body = _body(
        _page(
            options=[
                _option(id="a", body="![x](file:///tmp/a.png)"),
                _option(id="b", body="![y](file:///tmp/b.png)"),
            ]
        )
    )
    assert "twb-options-gallery" in body
    # Gallery mode never drawers an image behind a click. Scoped to the markup, not the
    # script — the script's own drawer-querying selector is a `twb-option-drawer` literal
    # regardless of whether the page has any.
    markup = body.split("<script", 1)[0]
    assert "twb-option-drawer" not in markup


def test_gallery_widens_the_page():
    html = _page(
        options=[
            _option(id="a", body="![x](file:///tmp/a.png)"),
            _option(id="b", body="![y](file:///tmp/b.png)"),
        ]
    )
    assert '<main class="twb-wide">' in html


def test_non_gallery_page_has_a_plain_main_tag():
    body = _body(_page(options=[_option()]))
    assert "<main>" in body
    assert "twb-wide" not in body


def test_multi_page_widens_when_any_step_is_a_gallery():
    html = _multi_page(
        questions=[
            {"question": "first", "options": [_option()]},
            {
                "question": "second",
                "options": [
                    _option(id="a", body="![x](file:///tmp/a.png)"),
                    _option(id="b", body="![y](file:///tmp/b.png)"),
                ],
            },
        ]
    )
    assert '<main class="twb-wide">' in html


def test_long_question_widens_the_page():
    from text_wall_breaker.markdown import WIDE_QUESTION_CHARS

    at_limit = "x" * WIDE_QUESTION_CHARS
    over = at_limit + "x"

    def single(question: str) -> str:
        return build_page(
            question=question,
            options=[_option()],
            multi_select=False,
            allow_free_text=True,
            submit_url="http://127.0.0.1:1/submit",
        )

    assert "twb-wide" not in _body(single(at_limit))
    assert '<main class="twb-wide">' in single(over)
    assert '<main class="twb-wide">' in _multi_page(
        questions=[
            {"question": "short", "options": [_option()]},
            {"question": over, "options": [_option()]},
        ]
    )


def test_lightbox_display_rule_is_guarded_against_the_hidden_attribute():
    # Regression: `.twb-lightbox { display: flex; ... }` alone beats the browser's own
    # `[hidden] { display: none }` (author origin outranks user-agent origin regardless
    # of specificity), which left the full-page overlay visible at all times — the whole
    # page reading as uniformly, wrongly dark, not just while an image was zoomed.
    for html in (_page(), _multi_page()):
        assert ".twb-lightbox[hidden] { display: none; }" in html


def test_multi_page_has_no_header_by_default():
    assert 'class="twb-header' not in _body(_multi_page())


def test_a_short_header_is_shown_flat_above_the_progress_bar():
    body = _body(_multi_page(header="Settled: **SQLite**."))
    header = '<div class="twb-header"><p>Settled: <strong>SQLite</strong>.</p></div>'
    assert header in body
    assert body.index(header) < body.index('class="twb-progress"')


def test_a_long_header_collapses_behind_a_context_toggle():
    body = _body(_multi_page(header="x " * 250))
    assert '<details class="twb-header">' in body
    assert "<summary>Context</summary>" in body


def test_a_long_header_that_is_one_list_counts_its_items():
    items = "\n".join(f"- decision number {i} " + "x " * 30 for i in range(7))
    body = _body(_multi_page(header=items))
    assert '<summary>Context <span class="twb-header-count">7 items</span></summary>' in body


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
    assert 'class="twb-question twb-long"' in html
    assert "<strong>Lead sentence.</strong>" in html


def test_a_picked_radio_shows_it_can_be_cleared_on_hover_on_both_pages():
    # Keyed on :checked and :not(:disabled), so it never shows on a checkbox, an empty
    # question, or a page frozen after Send.
    selector = '.twb-option:hover input[type="radio"]:checked:not(:disabled)'
    for html in (_page(), _multi_page()):
        assert selector in html
        assert 'content: "click again to clear"' not in html


def test_both_pages_offer_stop_here_and_send_it_as_an_interrupt():
    for html in (_page(), _multi_page()):
        assert 'id="twb-stop"' in html
        assert 'id="twb-stop-send" hidden disabled' in html
        assert "send('interrupt', data)" in html
        assert "__STOP__" not in html


def test_every_page_type_shares_one_action_row_stylesheet():
    from text_wall_breaker.page import build_review_page, build_show_page

    u = "http://127.0.0.1:1/submit"
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    pages = (
        _page(),
        _multi_page(),
        build_show_page(content="# R", submit_url=u),
        build_review_page(pages=[{"path": "a.py", "diff": diff}], submit_url=u),
    )
    for html in pages:
        assert html.count(".twb-actions .twb-actions-end") == 1
        # A colour rule keyed on an id alone outranks `:disabled` and keeps a disabled
        # primary solid; every one must be scoped to `:not(:disabled)`.
        assert "#twb-submit, #twb-next" not in html
        assert ".twb-actions button:disabled" in html
        # Beside the primary, inside its group: above or between the groups, showing or
        # hiding it moved the buttons.
        assert re.search(r'twb-actions-end">\s*<span id="twb-status"', html)


def test_wizard_segments_only_navigate_backwards():
    html = _multi_page()
    # An enabled segment is one before the current question; the script must never enable
    # the current or a later one, or a click could skip Next's answer check.
    assert "seg.disabled = frozen || segmentsLocked || i >= current;" in html
    assert "if (frozen || segmentsLocked || i >= current) return;" in html


def test_progress_fill_is_a_block_so_it_has_a_size():
    from text_wall_breaker.page import build_review_page, build_show_page

    u = "http://127.0.0.1:1/submit"
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    pages = (
        build_show_page(slides=["# 1", "# 2"], submit_url=u),
        build_review_page(pages=[{"path": "a.py", "diff": diff}], submit_url=u),
    )
    for html in pages:
        # An inline span ignores width and height, so the fill never showed.
        assert ".twb-progress-fill { display: block;" in html


def test_agent_notes_render_open_under_their_last_row_with_a_chip_on_the_first():
    diff = "@@ -1,2 +1,3 @@\n a\n+b\n+c\n"
    notes: list[ReviewNote] = [
        {"side": "new", "line": 2, "end_side": "new", "end_line": 3, "text": "Why **x**"}
    ]
    html = build_review_page(
        pages=[{"path": "a.py", "diff": diff, "notes": notes}], submit_url="http://x/submit"
    )
    body = html.split('<div class="twb-review-diff"', 1)[1]
    chip = body.index('class="twb-note-mark is-open" data-note="0"')
    note = body.index('<div class="twb-note is-open" data-note="0">')
    assert body.index('data-line="2"') < chip < body.index('data-line="3"') < note
    assert "Agent &middot; new 2\N{EN DASH}3" in body
    assert "<strong>x</strong>" in body[note:]
    assert "1 note from the agent" in html


def test_the_notes_bar_is_a_labelled_two_segment_switch():
    diff = "@@ -1,1 +1,2 @@\n a\n+b\n"
    notes: list[ReviewNote] = [{"side": "new", "line": 2, "text": "why"}]
    html = build_review_page(
        pages=[{"path": "a.md", "diff": diff, "notes": notes, "rendered": "<p>x</p>"}],
        submit_url="http://x/submit",
    )
    assert '<div class="twb-notes-bar" data-state="shown">' in html
    assert (
        '<span class="twb-switch"><button type="button" data-all="0">Hide all</button>'
        '<button type="button" data-all="1">Show all</button></span>'
    ) in html


def _head(header: str | None, blurb: str | None = None) -> str:
    diff = "@@ -1,1 +1,2 @@\n a\n+b\n"
    pages: list[ReviewPage] = [{"path": "src/pkg/a.py", "diff": diff, "blurb": blurb}]
    return build_review_page(pages=pages, header=header, submit_url="http://x/submit")


def test_the_header_is_a_slim_row_with_the_rest_folded_under_details():
    html = _head("# Retry rework\n\nWhy it changed.\n\n- one\n- two")
    assert '<h1 class="twb-head-title">Retry rework</h1>' in html
    assert '<span class="twb-chip"><b>1</b> file</span>' in html
    assert 'id="twb-chip-comments"><b>0</b> comments</span>' in html
    assert '<details class="twb-head-details"><summary>Details</summary>' in html
    assert "Why it changed." in html.split("<summary>Details</summary>", 1)[1]


def test_a_header_without_a_heading_is_not_hidden_and_none_is_just_the_row():
    open_html = _head("Just a paragraph.")
    assert '<span class="twb-head-title">Review</span>' in open_html
    assert '<details class="twb-head-details" open>' in open_html
    bare = _head(None).split('<header class="twb-head">', 1)[1].split("</header>", 1)[0]
    assert '<span class="twb-head-title">Review</span>' in bare
    assert "<details" not in bare
    assert "agent note" not in bare


def test_the_path_is_a_dim_folder_and_a_bold_file_and_the_blurb_is_wrapped():
    html = _head(None, blurb="The **core** change.")
    assert (
        '<div class="twb-review-path"><span class="twb-path-dir">src/pkg/</span>'
        '<span class="twb-path-file">a.py</span></div>'
    ) in html
    assert '<div class="twb-blurb"><p>The <strong>core</strong> change.</p></div>' in html


def test_switching_file_drops_an_open_selection_and_its_comment_box():
    pages: list[ReviewPage] = [
        {"path": "a.py", "diff": "@@ -1,1 +1,2 @@\n a\n+b\n"},
        {"path": "b.py", "diff": "@@ -1,1 +1,2 @@\n a\n+b\n"},
    ]
    html = build_review_page(pages=pages, submit_url="http://x/submit")
    # the file list announces every switch and the review script clears the selection on it
    assert "document.dispatchEvent(new CustomEvent('twb-file-change'))" in html
    assert (
        "document.addEventListener('twb-file-change', function () { if (sel) clearSel(); });"
        in html
    )


def test_a_page_without_notes_has_no_note_markup():
    html = _review("a.py")
    assert '<div class="twb-notes-bar">' not in html
    assert '<button type="button" class="twb-note-mark' not in html


@pytest.mark.parametrize("build", [_page, _multi_page, lambda: _review("a.py")])
def test_a_page_that_takes_an_answer_carries_the_export_button(build):
    html = build()
    assert "__EXPORT_SCRIPT__" not in html
    assert "twb-export-btn" in html and "#twb-export {" in html


def test_show_has_nothing_to_export():
    html = build_show_page(content="# R", submit_url="http://x")
    assert "twb-export" not in html


def test_the_export_script_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    asset = resources.files("text_wall_breaker").joinpath("assets", "export.js")
    result = subprocess.run(  # noqa: S603 — fixed argv: the node found above, our own asset
        [node, "--check", str(asset)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _placeholders(template: str) -> list[str]:
    text = resources.files("text_wall_breaker").joinpath("assets", template).read_text()
    return sorted(set(re.findall(r"__[A-Z_]+__", text)))


class _EventAttrs(HTMLParser):
    """Collects `on*` attributes: what text breaking out of an attribute turns into."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        self.found += [(tag, name) for name, _ in attrs if name.startswith("on")]


def _event_attrs(html: str) -> list[tuple[str, str]]:
    parser = _EventAttrs()
    parser.feed(html)
    return parser.found


def _breakout(placeholder: str) -> str:
    """Agent text that gains an `onfocus` attribute if `placeholder` is substituted."""
    return f"{placeholder} zz autofocus onfocus=f() yy="


_SUBMIT = "http://127.0.0.1:1/submit?t=T"
_STATUS = "http://127.0.0.1:1/status?t=T"


@pytest.mark.parametrize("placeholder", _placeholders("ask.html"))
def test_ask_placeholders_in_agent_text_stay_text(placeholder):
    text = _breakout(placeholder)
    html = build_page(
        question=f"`{text}`",
        options=[{"id": text, "label": text, "body": f"`{text}`"}],
        multi_select=False,
        allow_free_text=True,
        free_text_prefill=text,
        title=text,
        live="on",
        submit_url=_SUBMIT,
        status_url=_STATUS,
    )
    assert _event_attrs(html) == []
    assert html.count(placeholder) >= 4


@pytest.mark.parametrize("placeholder", _placeholders("multi.html"))
def test_multi_placeholders_in_agent_text_stay_text(placeholder):
    text = _breakout(placeholder)
    picked: dict[str, object] = {
        "id": text,
        "question": f"`{text}`",
        "options": [{"id": text, "label": text}],
        "multi_select": False,
        "allow_free_text": True,
    }
    ranked: dict[str, object] = {
        "id": "r",
        "question": "q",
        "rank": [text],
        "options": [{"id": text, "label": text}],
    }
    html = build_multi_page(
        questions=[picked, ranked],
        header=f"`{text}`",
        title=text,
        live="on",
        submit_url=_SUBMIT,
        status_url=_STATUS,
    )
    assert _event_attrs(html) == []
    assert html.count(placeholder) >= 4


@pytest.mark.parametrize("placeholder", _placeholders("show.html"))
@pytest.mark.parametrize("deck", [False, True])
def test_show_placeholders_in_agent_text_stay_text(placeholder, deck):
    text = _breakout(placeholder)
    content = f"`{text}` [x](http://h/{placeholder})"
    if deck:
        html = build_show_page(
            slides=[content], header=f"`{text}`", title=text, submit_url=_SUBMIT, status_url=_STATUS
        )
    else:
        html = build_show_page(content=content, title=text, submit_url=_SUBMIT, status_url=_STATUS)
    assert _event_attrs(html) == []
    assert placeholder in html


_MERMAID = "```mermaid\ngraph TD; A-->B\n```"


def _every_page() -> dict[str, str]:
    return {
        "ask": build_page(
            question=_MERMAID,
            options=[],
            multi_select=False,
            allow_free_text=True,
            live="on",
            submit_url=_SUBMIT,
            status_url=_STATUS,
        ),
        "multi": build_multi_page(
            questions=[{"question": _MERMAID, "options": []}],
            live="on",
            submit_url=_SUBMIT,
            status_url=_STATUS,
        ),
        "show": build_show_page(content=_MERMAID, submit_url=_SUBMIT, status_url=_STATUS),
        "deck": build_show_page(slides=[_MERMAID, "b"], submit_url=_SUBMIT, status_url=_STATUS),
        "review": build_review_page(
            pages=[{"path": "a.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}],
            submit_url=_SUBMIT,
            status_url=_STATUS,
            live="on",
        ),
    }


def _csp(html: str) -> str:
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert match, "no CSP meta tag"
    return match.group(1)


@pytest.mark.parametrize("name", ["ask", "multi", "show", "deck", "review"])
def test_every_page_has_a_nonce_csp_that_covers_each_of_its_scripts(name):
    html = _every_page()[name]
    policy = _csp(html)
    assert "default-src 'none'" in policy
    script_src = next(part for part in policy.split(";") if "script-src" in part)
    assert "'unsafe-inline'" not in script_src
    nonce = re.search(r"'nonce-([^']+)'", script_src)
    assert nonce
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags
    assert all(f'nonce="{nonce.group(1)}"' in tag for tag in tags)
    assert '<meta name="referrer" content="no-referrer">' in html


def test_a_script_tag_that_slipped_in_gets_no_nonce():
    # Should agent text ever reach the page unescaped, its <script> must not run.
    @page_module._fresh_nonce
    def build() -> str:
        return page_module._document(
            '<html><head></head><body>__CONTENT__<script nonce="__NONCE__"></script></body>',
            {"__CONTENT__": "<script>steal()</script>"},
        )

    html = build()
    nonce = re.search(r"'nonce-([^']+)'", html)
    assert nonce
    assert "<script>steal()</script>" in html
    assert html.count(f'nonce="{nonce.group(1)}"') == 1


def test_pages_build_from_templates_read_at_import(monkeypatch):
    # A running server must serve the templates its own code fills, not files edited on
    # disk since it started.
    def no_disk(*_args: object) -> None:
        raise AssertionError("asset read from disk after import")

    monkeypatch.setattr(resources, "files", no_disk)
    assert set(_every_page()) == {"ask", "multi", "show", "deck", "review"}


def test_a_page_may_only_reach_its_own_listener():
    policy = _csp(build_show_page(content="x", submit_url="http://127.0.0.1:4242/submit?t=T"))
    assert "connect-src http://127.0.0.1:4242;" in policy
    assert "connect-src 'none'" in _csp(build_show_page(content="x"))


def test_each_page_gets_its_own_nonce():
    first, second = (_csp(build_show_page(content="x")) for _ in range(2))
    assert first != second


@pytest.mark.parametrize("name", ["ask", "show"])
def test_mermaid_is_loaded_with_subresource_integrity(name):
    tag = re.search(r'<script[^>]*src="[^"]*mermaid[^"]*"[^>]*>', _every_page()[name])
    assert tag
    assert 'integrity="sha384-' in tag.group(0)
    assert 'crossorigin="anonymous"' in tag.group(0)


@pytest.mark.parametrize("name", ["ask", "multi", "show", "deck", "review"])
def test_without_mermaid_no_page_names_the_renderer_host_or_draws_a_diagram(name, monkeypatch):
    monkeypatch.setattr(config, "current", Config(mermaid=False))
    html = _every_page()[name]
    assert "jsdelivr" not in html and "<script" in html  # the page script is still there
    assert 'class="mermaid"' not in html
    assert (
        "script-src 'nonce-" in _csp(html)
        and "http" not in _csp(html).split("script-src")[1].split(";")[0]
    )
    if name != "review":  # a review page shows a diff of a.md, not the fence
        assert 'data-language="mermaid"' in html


def test_the_wizard_loads_mermaid_with_subresource_integrity_too():
    html = _every_page()["multi"]
    assert "script.integrity = " in html
    assert '"sha384-' in html

from __future__ import annotations

import os
import tempfile
import time

import pytest

from text_wall_breaker import show as show_module
from text_wall_breaker.page import build_show_page
from text_wall_breaker.show import InvalidShow, ShowRequest


def test_content_and_slides_together_are_rejected():
    request = ShowRequest(content="x", slides=["a"])
    with pytest.raises(InvalidShow, match="not both"):
        request.validated()


def test_empty_slides_list_is_rejected():
    with pytest.raises(InvalidShow, match="slides is empty"):
        ShowRequest(slides=[]).validated()


def test_all_blank_slides_are_rejected():
    with pytest.raises(InvalidShow, match="slides has no content"):
        ShowRequest(slides=["  ", "\n"]).validated()


def test_header_without_slides_is_rejected():
    with pytest.raises(InvalidShow, match="require slides"):
        ShowRequest(content="x", header="h").validated()


def test_footer_without_slides_is_rejected():
    with pytest.raises(InvalidShow, match="require slides"):
        ShowRequest(content="x", footer="f").validated()


def test_slides_request_validates_without_content():
    request = ShowRequest(slides=["one"], header="h", footer="f")
    assert request.validated() is request


def test_deck_page_renders_one_section_per_slide_only_first_active():
    html = build_show_page(slides=["# One", "# Two", "# Three"])
    assert html.count('class="deck-slide card is-active"') == 1
    assert html.count('class="deck-slide card">') == 2
    assert "<h1>One</h1>" in html
    assert "<h1>Two</h1>" in html
    assert "<h1>Three</h1>" in html


def test_deck_page_shows_progress_bar_and_nav_buttons():
    html = build_show_page(slides=["a", "b"])
    assert "Slide 1 of 2" in html
    assert 'class="pp-progress-fill" id="pp-deck-progress-fill" style="width: 50%"' in html
    assert "data-deck-prev" in html
    assert "data-deck-next" in html


def test_deck_page_renders_header_and_footer_once_each():
    html = build_show_page(slides=["slide body"], header="# Head", footer="# Foot")
    assert html.count("<h1>Head</h1>") == 1
    assert html.count("<h1>Foot</h1>") == 1


def test_plain_page_has_no_deck_script():
    html = build_show_page(content="just text")
    assert "data-deck-prev" not in html
    # base.css (shared by every page) always defines the .deck-slide rules — only their
    # use as an actual element class is what a non-deck page must not have.
    assert 'class="deck-slide' not in html


def test_a_diff_fence_with_dashed_lines_inside_a_slide_is_not_split():
    diff_slide = "```diff\n--- a/file\n+++ b/file\n-old\n+new\n```"
    html = build_show_page(slides=[diff_slide, "next slide"])
    assert html.count('class="deck-slide') == 2
    assert "next slide" in html


def test_deck_save_content_reconstructs_header_slides_and_footer():
    html = build_show_page(slides=["one", "two"], header="head", footer="foot")
    assert "head\\n\\none\\n\\n---\\n\\ntwo\\n\\nfoot" in html


def test_a_report_in_its_own_tab_is_named_by_show_status(monkeypatch):
    class Busy:
        def try_claim(self):
            return False

        def report_status(self):
            return {"state": "none"}

    monkeypatch.setattr(show_module, "get_shared_session", lambda: Busy())
    monkeypatch.setattr(show_module, "open_in_browser", lambda path: None)
    monkeypatch.setattr(show_module._own_tab_title, "value", None)
    result = show_module.run_show(ShowRequest(content="# R", title="Report"))
    assert result["opened"] is True
    assert show_module.show_status() == {"state": "none", "own_tab": "Report"}
    show_module._own_tab_title.value = None
    assert show_module.show_status() == {"state": "none"}


def test_a_day_old_one_off_report_is_removed_by_the_next(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old, fresh = tmp_path / "text-wall-breaker-show-old", tmp_path / "text-wall-breaker-show-new"
    other = tmp_path / "something-else"
    for directory in (old, fresh, other):
        directory.mkdir()
        (directory / "show.html").write_text("x")
    day_ago = time.time() - 25 * 3600
    os.utime(old, (day_ago, day_ago))
    os.utime(other, (day_ago, day_ago))
    link = tmp_path / "text-wall-breaker-show-link"
    link.symlink_to(other)

    show_module._sweep_old_reports()

    assert not old.exists()
    assert fresh.exists()
    assert other.exists()
    assert link.is_symlink()

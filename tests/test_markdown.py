from __future__ import annotations

import pytest

from pretty_prompt.markdown import render_inline, render_markdown


def test_empty_input_renders_nothing():
    assert render_markdown("") == ""
    assert render_markdown("   \n  \n") == ""


def test_paragraph_keeps_authored_line_breaks():
    assert render_markdown("one\ntwo") == "<p>one<br>two</p>"


def test_blank_line_separates_paragraphs():
    assert render_markdown("one\n\ntwo") == "<p>one</p><p>two</p>"


@pytest.mark.parametrize("level", range(1, 7))
def test_headings(level):
    hashes = "#" * level
    assert render_markdown(f"{hashes} Title") == f"<h{level}>Title</h{level}>"


def test_seven_hashes_is_not_a_heading():
    assert render_markdown("####### Title") == "<p>####### Title</p>"


def test_bullet_list():
    assert render_markdown("- one\n- two") == "<ul><li>one</li><li>two</li></ul>"


def test_ordered_list():
    assert render_markdown("1. one\n2. two") == "<ol><li>one</li><li>two</li></ol>"


def test_switching_marker_starts_a_new_list():
    assert render_markdown("1. one\n- two") == "<ol><li>one</li></ol><ul><li>two</li></ul>"


def test_fenced_code_block_keeps_its_language():
    out = render_markdown("```python\nx = 1\n```")
    assert out == '<pre class="code-block" data-language="python"><code>x = 1</code></pre>'


def test_unterminated_fence_renders_what_it_has():
    assert render_markdown("```\nx = 1") == '<pre class="code-block"><code>x = 1</code></pre>'


def test_markers_inside_a_code_span_stay_literal():
    assert render_inline("`**x**`") == "<code>**x**</code>"


def test_bold_wins_over_italic():
    assert render_inline("**x**") == "<strong>x</strong>"
    assert render_inline("*x*") == "<em>x</em>"


def test_nested_emphasis():
    assert render_inline("**a *b***") == "<strong>a <em>b</em></strong>"


def test_link_and_image():
    assert render_inline("[t](https://e.test)") == '<a href="https://e.test">t</a>'
    assert render_inline("![a](file:///tmp/x.png)") == '<img src="file:///tmp/x.png" alt="a">'


# The page is opened from disk with file:// access, so a javascript: URL in a prompt
# would otherwise be one click from executing in the same origin as the answer form.
@pytest.mark.parametrize(
    "source",
    [
        "[t](javascript:alert(1))",
        "![a](javascript:alert(1))",
        "[t](vbscript:x)",
        "[t](relative/path.png)",
    ],
)
def test_unsafe_or_schemeless_urls_render_as_text(source):
    out = render_inline(source)
    assert "href=" not in out
    assert "src=" not in out


@pytest.mark.parametrize("value", ["success", "warning", "danger", "info"])
def test_badge_renders_with_a_valid_severity_value(value):
    out = render_inline(f"!badge[recommended]({value})")
    assert out == f'<span class="badge" data-axis="severity" data-value="{value}">recommended</span>'


def test_badge_with_an_unrecognized_value_falls_through_to_text():
    out = render_inline("!badge[bogus](nonsense)")
    assert out == "!badge[bogus](nonsense)"
    assert "<span" not in out


def test_badge_text_renders_inline_markdown():
    out = render_inline("!badge[**x**](success)")
    assert "<strong>x</strong>" in out


def test_verdict_fence_renders_as_a_verdict_role_div():
    out = render_markdown("```verdict\nUse Nexus CE.\n```")
    assert out == '<div data-role="verdict">Use Nexus CE.</div>'


def test_verdict_fence_renders_inline_markdown_not_code():
    out = render_markdown("```verdict\n**Use** it.\n```")
    assert out == '<div data-role="verdict"><strong>Use</strong> it.</div>'


def test_raw_html_is_escaped_everywhere():
    assert "<script>" not in render_markdown("<script>alert(1)</script>")
    assert "<b>" not in render_markdown("# <b>x</b>")
    assert "<i>" not in render_markdown("- <i>x</i>")
    assert "<s>" not in render_markdown("```\n<s>x</s>\n```")


def test_quotes_are_escaped_in_alt_text():
    out = render_inline('![a" onerror="x](file:///tmp/x.png)')
    assert 'onerror="x"' not in out


def test_table_renders_header_and_body():
    out = render_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |")
    assert out == (
        "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
    )


def test_table_alignment_markers_become_inline_styles():
    out = render_markdown("| A | B | C |\n| :-- | :-: | --: |\n| 1 | 2 | 3 |")
    assert '<th style="text-align:left">A</th>' in out
    assert '<th style="text-align:center">B</th>' in out
    assert '<th style="text-align:right">C</th>' in out


def test_table_cells_render_inline_markdown():
    out = render_markdown("| A |\n| --- |\n| **bold** |")
    assert "<td><strong>bold</strong></td>" in out


def test_table_ends_at_a_blank_line_or_non_table_line():
    out = render_markdown("| A |\n| --- |\n| 1 |\n\nafter")
    assert out == (
        "<table><thead><tr><th>A</th></tr></thead>"
        "<tbody><tr><td>1</td></tr></tbody></table><p>after</p>"
    )


def test_a_pipe_without_a_separator_row_is_not_a_table():
    assert render_markdown("a | b") == "<p>a | b</p>"


def test_diff_block_classifies_added_and_removed_lines():
    out = render_markdown("```diff\n def f():\n-    old()\n+    new()\n```")
    assert out == (
        '<div class="pp-diff">'
        '<div class="line ctx">def f():</div>'
        '<div class="line del">    old()</div>'
        '<div class="line add">    new()</div>'
        "</div>"
    )


def test_diff_block_marks_hunk_and_file_headers():
    out = render_markdown("```diff\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n```")
    assert '<div class="line file">--- a/x.py</div>' in out
    assert '<div class="line file">+++ b/x.py</div>' in out
    assert '<div class="line hunk">@@ -1,2 +1,2 @@</div>' in out


def test_diff_block_escapes_line_content():
    out = render_markdown("```diff\n+<script>\n```")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_diff_block_pads_empty_lines():
    out = render_markdown("```diff\n \n```")
    assert '<div class="line ctx">&nbsp;</div>' in out


def test_blockquote_renders_as_blockquote():
    assert render_markdown("> quoted") == "<blockquote><p>quoted</p></blockquote>"


def test_blockquote_marker_without_a_space_is_still_recognised():
    assert render_markdown(">quoted") == "<blockquote><p>quoted</p></blockquote>"


def test_multiline_blockquote_keeps_authored_line_breaks():
    out = render_markdown("> one\n> two")
    assert out == "<blockquote><p>one<br>two</p></blockquote>"


def test_blockquote_renders_inline_markdown():
    out = render_markdown("> **bold** text")
    assert out == "<blockquote><p><strong>bold</strong> text</p></blockquote>"


def test_blockquote_ends_at_a_blank_line_or_non_quote_line():
    out = render_markdown("> quoted\n\nafter")
    assert out == "<blockquote><p>quoted</p></blockquote><p>after</p>"

    out = render_markdown("> quoted\nafter")
    assert out == "<blockquote><p>quoted</p></blockquote><p>after</p>"


def test_strikethrough():
    assert render_inline("~~gone~~") == "<del>gone</del>"


def test_strikethrough_nests_other_emphasis():
    assert render_inline("~~**gone**~~") == "<del><strong>gone</strong></del>"


def test_nested_bullet_list():
    out = render_markdown("- one\n  - one.a\n- two")
    assert out == "<ul><li>one<ul><li>one.a</li></ul></li><li>two</li></ul>"


def test_nested_ordered_list():
    out = render_markdown("1. one\n   1. one.a\n2. two")
    assert out == "<ol><li>one<ol><li>one.a</li></ol></li><li>two</li></ol>"


def test_nested_list_mixed_markers():
    out = render_markdown("- one\n  1. one.a\n  2. one.b\n- two")
    assert out == (
        "<ul><li>one<ol><li>one.a</li><li>one.b</li></ol></li><li>two</li></ul>"
    )


def test_multiple_nesting_levels():
    out = render_markdown("- a\n  - b\n    - c")
    assert out == "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"

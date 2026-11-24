from __future__ import annotations

import time
from pathlib import Path

import pytest

from text_wall_breaker.markdown import render_inline, render_markdown


def test_empty_input_renders_nothing():
    assert render_markdown("") == ""
    assert render_markdown("   \n  \n") == ""


def test_paragraph_keeps_authored_line_breaks():
    assert render_markdown("one\ntwo") == "<p>one<br>two</p>"


def test_blank_line_separates_paragraphs():
    assert render_markdown("one\n\ntwo") == "<p>one</p><p>two</p>"


def test_literal_backslash_n_escape_is_treated_as_a_line_break():
    assert render_markdown("one\\ntwo") == "<p>one<br>two</p>"


def test_literal_backslash_n_n_escape_separates_paragraphs():
    assert render_markdown("one\\n\\ntwo") == "<p>one</p><p>two</p>"


def test_literal_backslash_n_inside_a_fence_is_not_expanded():
    out = render_markdown("```\na\\nb\n```")
    assert out == '<pre class="code-block"><code>a\\nb</code></pre>'


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
    assert out == (
        '<pre class="code-block" data-language="python"><code>'
        '<span class="tk-n">x</span> <span class="tk-o">=</span> <span class="tk-mi">1</span>'
        "</code></pre>"
    )


def test_unterminated_fence_renders_what_it_has():
    assert render_markdown("```\nx = 1") == '<pre class="code-block"><code>x = 1</code></pre>'


def test_markers_inside_a_code_span_stay_literal():
    assert render_inline("`**x**`") == "<code>**x**</code>"


def test_double_backtick_code_span_can_contain_a_single_backtick():
    assert render_inline("``a ` b``") == "<code>a ` b</code>"


def test_unmatched_triple_backtick_in_prose_stays_literal():
    # The tick run used to backtrack to a shorter length when no closing run of the same
    # length existed, pairing the first two backticks into a spurious empty <code></code>
    # and leaking the third as text, instead of falling through to literal text like every
    # other unmatched construct here does.
    text = "``` not a real fence, just talking about backticks"
    assert render_inline(text) == text


def test_bold_wins_over_italic():
    assert render_inline("**x**") == "<strong>x</strong>"
    assert render_inline("*x*") == "<em>x</em>"


def test_nested_emphasis():
    assert render_inline("**a *b***") == "<strong>a <em>b</em></strong>"


@pytest.mark.parametrize("text", ["2 * 3 * 4", "2 ** 3 ** 4", "f(*args, **kwargs)", "* x *"])
def test_a_marker_with_space_on_its_inner_side_is_not_emphasis(text):
    assert render_inline(text) == text


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("**a**, **b**", "<strong>a</strong>, <strong>b</strong>"),
        ("*x* *y*", "<em>x</em> <em>y</em>"),
        ("*a* and *b*", "<em>a</em> and <em>b</em>"),
        ("**ab** and **cd**", "<strong>ab</strong> and <strong>cd</strong>"),
    ],
)
def test_neighbouring_short_spans_stay_apart(text, html):
    assert render_inline(text) == html


def test_a_fence_line_with_a_language_inside_a_block_does_not_close_it():
    html = render_markdown("```\nfoo\n```python\nbar\n```\nafter")
    assert html.count("<pre") == 1
    assert "```python\nbar" in html
    assert "<p>after</p>" in html


# An agent writes identifiers like set_mode in running prose far more often than it
# means underscore emphasis mid-word, so `_` only delimits at a word boundary (as in
# CommonMark); `*` still works intraword.
@pytest.mark.parametrize(
    "source",
    ["set_mode and post_message", "a_b_c", "x__y__z", "my__init__py"],
)
def test_intraword_underscores_stay_literal(source):
    assert render_inline(source) == source


def test_underscore_emphasis_at_word_boundaries():
    assert render_inline("an _x_ here") == "an <em>x</em> here"
    assert render_inline("an __x__ here") == "an <strong>x</strong> here"
    assert render_inline("_snake_case_") == "<em>snake_case</em>"
    assert render_inline("un*frigging*believable") == "un<em>frigging</em>believable"


def test_link_and_image():
    assert render_inline("[t](https://e.test)") == '<a href="https://e.test">t</a>'
    assert render_inline("![a](file:///tmp/x.png)") == '<img src="file:///tmp/x.png" alt="a">'


def test_link_url_keeps_a_parenthesised_segment():
    # `[^)\s]+` used to stop at the first `)`, truncating the href and leaking the rest
    # of the URL (and the markdown's own closing paren) as plain sibling text.
    out = render_inline("[text](http://example.com/a(b)c)")
    assert out == '<a href="http://example.com/a(b)c">text</a>'


def test_image_url_keeps_a_parenthesised_segment():
    out = render_inline("![a](file:///tmp/x(1).png)")
    assert out == '<img src="file:///tmp/x(1).png" alt="a">'


@pytest.mark.parametrize(
    "source",
    [
        "[t](" + "a" * 5000,
        "[t](" + "a" * 5000 + " tail",
        "![x](" + "a" * 5000 + " tail)",
        "[t](" + "(a)" * 2000 + " tail",
    ],
)
def test_an_unclosed_link_url_does_not_hang(source):
    # The nested quantifier in the URL pattern took exponential time here: 26 characters
    # already cost seconds.
    start = time.perf_counter()
    render_inline(source)
    assert time.perf_counter() - start < 1


def _render_seconds(source: str) -> float:
    start = time.perf_counter()
    render_inline(source)
    return time.perf_counter() - start


@pytest.mark.parametrize("unit", ["`", "[", "![", "!badge[", " _a", "__a ", "*", "*a ", "**a "])
def test_a_long_run_of_one_marker_renders_in_linear_time(unit):
    # 64k characters: quadratic matching took tens of seconds, nesting overflowed the stack.
    # A ratio, not a wall-clock bound: 8x the input costs ~8x when linear, ~64x when not.
    small = _render_seconds(unit * (8_000 // len(unit)))
    large = _render_seconds(unit * (64_000 // len(unit)))
    assert large < max(small, 0.005) * 24


def test_emphasis_nests_only_so_deep():
    html = render_inline("*" * 2000)
    assert html.count("<strong>") + html.count("<em>") <= 9


def test_image_bare_path_is_a_file_url():
    assert render_inline("![a](/tmp/x.png)") == '<img src="file:///tmp/x.png" alt="a">'
    home = Path.home()
    assert render_inline("![a](~/x.png)") == f'<img src="file://{home}/x.png" alt="a">'


def test_image_bare_path_goes_through_asset_route():
    out = render_inline("![a](/tmp/x.png)", asset_base_url="http://h/asset?t=k")
    assert out == '<img src="http://h/asset?t=k&amp;path=%2Ftmp%2Fx.png" alt="a">'


def test_image_file_url_is_percent_decoded_for_the_asset_route():
    out = render_inline("![a](file:///tmp/a%20b.png)", asset_base_url="http://h/asset?t=k")
    assert out == '<img src="http://h/asset?t=k&amp;path=%2Ftmp%2Fa%20b.png" alt="a">'


@pytest.mark.parametrize("url", ["https://e.test/beacon.png", "http://e.test/x.png"])
def test_a_remote_image_stays_text(url):
    # Loading it would tell that server the page was opened, and from where.
    assert render_inline(f"![a]({url})") == f"![a]({url})"


def test_image_relative_and_link_paths_stay_text():
    assert render_inline("![a](x.png)") == "![a](x.png)"
    assert render_inline("![a](//evil.test/x.png)") == "![a](//evil.test/x.png)"
    assert render_inline("[t](/tmp/x)") == "[t](/tmp/x)"


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
    assert (
        out == f'<span class="badge" data-axis="severity" data-value="{value}">recommended</span>'
    )


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
        '<div class="table-wrap"><table><thead><tr><th>A</th><th>B</th></tr></thead>'
        "<tbody><tr><td>1</td><td>2</td></tr></tbody></table></div>"
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
        '<div class="table-wrap"><table><thead><tr><th>A</th></tr></thead>'
        "<tbody><tr><td>1</td></tr></tbody></table></div><p>after</p>"
    )


def test_a_pipe_without_a_separator_row_is_not_a_table():
    assert render_markdown("a | b") == "<p>a | b</p>"


def test_a_list_item_with_a_pipe_stays_a_list_even_before_a_separator_line():
    # A bullet line always wins over table detection, even when it contains a `|` and is
    # immediately followed by a line shaped like a separator row: it used to lose its
    # `- ` marker into a bogus single-row table instead of staying a list.
    out = render_markdown("- item | with pipe\n| --- |")
    assert "<table" not in out
    assert out.startswith("<ul><li>item | with pipe</li></ul>")


def test_diff_block_classifies_added_and_removed_lines():
    out = render_markdown("```diff\n def f():\n-    old()\n+    new()\n```")
    assert out == (
        '<div class="twb-diff">'
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
    assert out == ("<ul><li>one<ol><li>one.a</li><li>one.b</li></ol></li><li>two</li></ul>")


def test_multiple_nesting_levels():
    out = render_markdown("- a\n  - b\n    - c")
    assert out == "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"


def test_bold_lead_bolds_first_sentence_of_a_long_question():
    from text_wall_breaker.markdown import bold_lead

    question = "Minor `a.py:82`: the rename leaves old names. " + "More detail. " * 25
    bolded = bold_lead(question)
    assert bolded is not None
    assert bolded.startswith("**Minor `a.py:82`: the rename leaves old names.** More")


def test_bold_lead_leaves_short_or_leadless_questions_alone():
    from text_wall_breaker.markdown import bold_lead

    assert bold_lead("Short question?") is None
    assert bold_lead("x" * 300) is None
    assert bold_lead("y" * 250 + ". tail") is None


def test_bold_lead_does_not_nest_inside_an_already_bold_lead():
    from text_wall_breaker.markdown import bold_lead, render_inline

    question = "**Important**. " + "padding " * 30 + "more context past the threshold."
    bolded = bold_lead(question)
    assert bolded is None
    # The question renders correctly when left alone, instead of corrupting the nesting.
    assert render_inline(question) == (
        "<strong>Important</strong>. " + "padding " * 30 + "more context past the threshold."
    )


def test_bold_lead_skips_a_terminator_inside_a_markdown_link():
    from text_wall_breaker.markdown import bold_lead

    # The `. ` inside the link text used to end the lead there, splicing the closing
    # `**` into the middle of `[...](...)` and breaking the link entirely.
    pad = " padding word" * 30
    question = f"See [the full write-up. ](https://example.com/a) for context.{pad}"
    bolded = bold_lead(question)
    assert bolded is not None
    assert bolded.startswith("**See [the full write-up. ](https://example.com/a) for context.**")
    assert render_inline(bolded).startswith(
        '<strong>See <a href="https://example.com/a">the full write-up. </a> for context.</strong>'
    )


def test_thematic_break_renders_as_hr():
    assert render_markdown("before\n\n---\n\nafter") == "<p>before</p><hr><p>after</p>"


def test_thematic_break_variants():
    for marker in ("---", "***", "___", "----", "******"):
        assert render_markdown(marker) == "<hr>"


def test_short_dash_runs_are_not_a_thematic_break():
    assert render_markdown("--") == "<p>--</p>"


def test_table_separator_without_a_pipe_is_a_thematic_break_not_a_table():
    # A divider right after a line that happens to contain `|` must not be swallowed
    # into a bogus single-cell table — see _is_table_separator's pipe requirement.
    out = render_markdown("p99 | throughput both fine\n---\nafter")
    assert out == "<p>p99 | throughput both fine</p><hr><p>after</p>"


def test_fence_with_a_known_language_is_syntax_colored():
    out = render_markdown("```python\ndef f():\n    return '<x>'\n```")
    assert out.startswith('<pre class="code-block" data-language="python"><code>')
    assert '<span class="tk-k">def</span>' in out
    assert "<x>" not in out
    assert "&lt;x&gt;" in out


def test_fence_with_an_unknown_language_stays_plain():
    out = render_markdown("```nosuchlang\na < b\n```")
    assert out == '<pre class="code-block" data-language="nosuchlang"><code>a &lt; b</code></pre>'

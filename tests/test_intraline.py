from __future__ import annotations

import html
import re

import pytest

from pretty_prompt.diff import parse_unified_diff
from pretty_prompt.intraline import changed_ranges, decorated_rows, mark_ranges


def text_of(row_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", row_html))


def marked(row_html: str) -> list[str]:
    return [html.unescape(m) for m in re.findall(r'<mark class="pp-chg">(.*?)</mark>', row_html)]


def alike(old: str, new: str):
    found = changed_ranges(old, new)
    assert found is not None
    return found


def test_a_changed_word_is_marked_whole_on_both_sides():
    old, new = "total = price * count", "total = price * quantity"
    old_ranges, new_ranges = alike(old, new)
    assert [old[a:b] for a, b in old_ranges] == ["count"]
    assert [new[a:b] for a, b in new_ranges] == ["quantity"]


def test_an_insertion_marks_only_the_new_side():
    old_ranges, new_ranges = alike("call(a)", "call(a, b)")
    assert old_ranges == []
    assert [("call(a, b)")[a:b] for a, b in new_ranges] == [", b"]


def test_changes_separated_by_spaces_merge():
    old_ranges, _ = alike("keep one two keep keep", "keep three four keep keep")
    assert [("keep one two keep keep")[a:b] for a, b in old_ranges] == ["one two"]


def test_unrelated_lines_are_not_marked():
    assert changed_ranges("import os", "def main(argv):") is None


def test_identical_and_huge_lines_are_not_marked():
    assert changed_ranges("same", "same") is None
    assert changed_ranges("x " * 2000, "y " * 2000) is None


def test_marks_never_cross_a_tag_and_keep_the_text():
    row = '<span class="tk-k">def</span> <span class="tk-nf">foo</span>(a)'
    out = mark_ranges(row, [(1, 8)])  # "ef foo" and a bit
    assert text_of(out) == "def foo(a)"
    assert out.count("<mark") == out.count("</mark>")
    # every mark sits inside a single text run: no tag between its open and close
    assert not re.search(r"<mark[^>]*>[^<]*<(?!/mark)", out)


def test_entities_count_as_one_character():
    out = mark_ranges("a &lt; b &amp;&amp; c", [(2, 3)])
    assert marked(out) == ["<"]
    assert text_of(out) == "a < b && c"


DIFF = """@@ -1,4 +1,4 @@
 keep
-total = price * count
+total = price * quantity
 tail
"""


def test_decorated_rows_mark_a_paired_edit_in_a_file_without_a_lexer():
    rows = parse_unified_diff(DIFF)
    out = decorated_rows("notes.unknownext", rows)
    assert marked(out[2]) == ["count"] and marked(out[3]) == ["quantity"]
    assert 1 not in out and 4 not in out


def test_decorated_rows_keep_the_syntax_colors():
    rows = parse_unified_diff(DIFF.replace("total", "x").replace("price", "1"))
    out = decorated_rows("x.py", rows)
    assert "tk-" in out[2] and "pp-chg" in out[2]
    assert text_of(out[2]) == "x = 1 * count" and text_of(out[3]) == "x = 1 * quantity"


def test_a_lone_deletion_or_addition_is_left_alone():
    only_del = parse_unified_diff("@@ -1,2 +1,1 @@\n keep\n-gone\n")
    only_add = parse_unified_diff("@@ -1,1 +1,2 @@\n keep\n+new\n")
    for rows in (only_del, only_add):
        assert not any("pp-chg" in value for value in decorated_rows("x.txt", rows).values())


@pytest.mark.parametrize("count", [2, 3])
def test_runs_pair_in_order(count):
    old = "".join(f"-line {i} old\n" for i in range(count))
    new = "".join(f"+line {i} new\n" for i in range(count))
    out = decorated_rows("x.txt", parse_unified_diff(f"@@ -1,{count} +1,{count} @@\n{old}{new}"))
    assert [marked(out[i]) for i in range(1, count + 1)] == [["old"]] * count
    assert [marked(out[i]) for i in range(count + 1, 2 * count + 1)] == [["new"]] * count

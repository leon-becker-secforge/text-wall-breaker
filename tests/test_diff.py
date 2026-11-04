from __future__ import annotations

import difflib

import pytest

from pretty_prompt.diff import (
    Gap,
    expansion_lines,
    gaps_of,
    rows_with_context,
    split_lines,
)

OLD = [f"line {k}" for k in range(1, 31)]
NEW = list(OLD)
NEW[4] = "line 5 changed"
NEW.insert(20, "line 20b added")  # shifts everything after it by one


def _diff(old: list[str], new: list[str], context: int = 3) -> str:
    return "".join(
        difflib.unified_diff(
            [line + "\n" for line in old], [line + "\n" for line in new], "a/f", "b/f", n=context
        )
    )


def test_split_lines_drops_line_endings_and_the_final_newline():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\r\nb") == ["a", "b"]
    assert split_lines("") == []
    assert split_lines("a\n\nb\n") == ["a", "", "b"]


def test_gaps_are_the_stretches_before_between_and_after_the_hunks():
    assert gaps_of(_diff(OLD, NEW), len(NEW)) == [
        Gap(1, 1, 0, "top", 0),
        Gap(9, 17, 0, "between", 1),
        Gap(25, 31, -1, "end", 2),  # one added line above: old = new - 1
    ]


def test_a_gap_knows_its_size():
    assert Gap(9, 16, 0, "between", 1).size == 8


def test_no_gap_where_hunks_touch_the_file_edges():
    assert gaps_of(_diff(["a"], ["b"]), 1) == []


def test_a_diff_that_does_not_fit_the_file_has_no_gaps():
    assert gaps_of(_diff(OLD, NEW), 5) == []


def test_a_pure_deletion_hunk_still_numbers_the_lines_after_it():
    old = ["a", "b", "c", "d", "e"]
    new = ["c", "d", "e"]
    [gap] = gaps_of(_diff(old, new, context=0), len(new))
    assert gap == Gap(1, 3, 2, "end", 1)  # new 1 is old 3


def test_rows_with_context_is_the_whole_file_in_order_with_true_numbers():
    rows = rows_with_context(_diff(OLD, NEW), NEW)
    new_numbers = [row.new_line for row in rows if row.new_line is not None]
    assert new_numbers == list(range(1, len(NEW) + 1))
    for row in rows:
        if row.new_line is not None:
            assert NEW[row.new_line - 1] == row.text
        if row.old_line is not None:
            assert OLD[row.old_line - 1] == row.text


def test_rows_with_context_keeps_a_deleted_row_where_the_diff_has_it():
    rows = rows_with_context(_diff(OLD, NEW), NEW)
    kinds = [row.kind for row in rows]
    assert kinds.count("del") == 1
    assert kinds.count("add") == 2
    assert rows[kinds.index("del")].text == "line 5"


def test_expansion_lines_accepts_the_file_the_diff_came_from():
    assert expansion_lines(_diff(OLD, NEW), "\n".join(NEW) + "\n") == NEW


@pytest.mark.parametrize(
    "text",
    [
        "\n".join(NEW[:3]),  # too short
        "\n".join([*NEW[:4], "edited", *NEW[5:]]),  # a hunk row differs
    ],
)
def test_expansion_lines_refuses_a_file_that_is_not_the_diffs_new_side(text):
    assert expansion_lines(_diff(OLD, NEW), text) is None

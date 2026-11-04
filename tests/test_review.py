from __future__ import annotations

import difflib

import pytest

from pretty_prompt.listener import Submission
from pretty_prompt.review import Page, ReviewRequest, _result_from_submission

DIFF = "@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"
PAGES = [Page(path="a.py", diff=DIFF)]
COMMENT = {"path": "a.py", "side": "new", "line": 2, "text": " why? "}


def test_submit_returns_comments_with_their_context():
    submission = Submission(kind="submit", data={"comments": [COMMENT], "overall_note": " ok "})
    assert _result_from_submission(PAGES, submission, None) == {
        "comments": [{**COMMENT, "text": "why?", "context": "new"}],
        "page_notes": [],
        "overall_note": "ok",
    }


def test_page_notes_are_returned_trimmed_and_blank_ones_dropped():
    data = {
        "page_notes": [
            {"path": "a.py", "text": " split this file "},
            {"path": "a.py", "text": "   "},
        ]
    }
    result = _result_from_submission(PAGES, Submission(kind="submit", data=data), None)
    assert result["page_notes"] == [{"path": "a.py", "text": "split this file"}]


def test_a_page_note_for_an_unknown_path_or_of_the_wrong_shape_is_dropped():
    data = {
        "page_notes": [
            {"path": "other.py", "text": "x"},
            {"path": "a.py", "text": 3},
            "not a dict",
        ]
    }
    result = _result_from_submission(PAGES, Submission(kind="submit", data=data), None)
    assert result["page_notes"] == []


def test_abandoned_keeps_the_page_notes_of_the_last_report():
    progress = {"page_notes": [{"path": "a.py", "text": "wip"}]}
    result = _result_from_submission(PAGES, None, progress)
    assert result["page_notes"] == [{"path": "a.py", "text": "wip"}]


def test_cancel_keeps_the_comments_and_says_so():
    submission = Submission(kind="cancel", data={"comments": [COMMENT], "overall_note": ""})
    result = _result_from_submission(PAGES, submission, None)
    assert result["cancelled"] is True
    assert len(result["comments"]) == 1


def test_abandoned_returns_the_last_progress_report():
    progress = {"comments": [COMMENT], "overall_note": "half done"}
    result = _result_from_submission(PAGES, None, progress)
    assert result["abandoned"] is True
    assert "cancelled" not in result
    assert result["overall_note"] == "half done"
    assert [c["text"] for c in result["comments"]] == ["why?"]


def test_abandoned_without_any_report_is_an_empty_review():
    assert _result_from_submission(PAGES, None, None) == {
        "comments": [],
        "page_notes": [],
        "overall_note": None,
        "abandoned": True,
    }


def test_abandoned_progress_with_an_unknown_line_is_dropped():
    progress = {"comments": [{**COMMENT, "line": 99}], "overall_note": ""}
    assert _result_from_submission(PAGES, None, progress)["comments"] == []


RANGE_DIFF = "@@ -1,3 +1,4 @@\n keep\n-old\n+new\n+added\n tail\n"
RANGE_PAGES = [Page(path="r.py", diff=RANGE_DIFF)]
ADDED = {"side": "new", "line": 2, "end_side": "new", "end_line": 3}


def _comments(*entries):
    data = {"comments": [{"path": "r.py", "text": "t", **entry} for entry in entries]}
    submission = Submission(kind="submit", data=data)
    return _result_from_submission(RANGE_PAGES, submission, None)["comments"]


def test_a_range_on_one_side_returns_its_end_and_marked_rows():
    [comment] = _comments(ADDED)
    assert comment == {
        "path": "r.py",
        "side": "new",
        "line": 2,
        "end_side": "new",
        "end_line": 3,
        "text": "t",
        "context": "+new\n+added",
    }


def test_a_range_may_start_on_a_deleted_row_and_end_on_an_added_one():
    [comment] = _comments({"side": "old", "line": 2, "end_side": "new", "end_line": 3})
    assert (comment["side"], comment["end_side"]) == ("old", "new")
    assert comment["context"] == "-old\n+new\n+added"


def test_a_range_covers_every_row_between_its_ends_in_display_order():
    # new 1..2 spans the deleted row that sits between them on screen.
    [comment] = _comments({"side": "new", "line": 1, "end_side": "new", "end_line": 2})
    assert comment["context"] == " keep\n-old\n+new"


def test_a_single_line_comment_keeps_the_shape_and_plain_context_of_0_9():
    [comment] = _comments({"side": "new", "line": 2})
    assert list(comment) == ["path", "side", "line", "text", "context"]
    assert comment["context"] == "new"


def test_a_range_ending_where_it_starts_is_a_single_line_comment():
    [comment] = _comments({"side": "new", "line": 2, "end_side": "new", "end_line": 2})
    assert "end_side" not in comment
    assert "end_line" not in comment


@pytest.mark.parametrize(
    "end",
    [
        {"end_side": "new", "end_line": 1},  # before the start
        {"end_side": "new", "end_line": 99},  # no such row
        {"end_side": "old", "end_line": 3},  # old line 3 is context, addressed as new 4
        {"end_side": "new"},  # half an end
        {"end_line": 3},
        {"end_side": "sideways", "end_line": 3},
        {"end_side": "new", "end_line": True},
    ],
)
def test_a_comment_with_a_bad_end_is_dropped(end):
    assert _comments({"side": "new", "line": 2, **end}) == []


def test_a_quote_inside_the_rows_is_returned_trimmed():
    [comment] = _comments({**ADDED, "quote": " ew\nadd "})
    assert comment["quote"] == "ew\nadd"


def test_a_quote_compares_whitespace_loosely():
    [comment] = _comments({**ADDED, "quote": "ew  add"})
    assert comment["quote"] == "ew  add"


@pytest.mark.parametrize("quote", ["not in the rows", "", "   ", 3, "new\nadded"])
def test_a_bad_or_whole_quote_is_dropped_but_the_comment_stays(quote):
    [comment] = _comments({**ADDED, "quote": quote})
    assert "quote" not in comment


# A file where the diff shows lines 2-8 and 28-34 of 40; lines 1 and 9-27 and 35-40 are hidden.
LONG_OLD = [f"line {k}" for k in range(1, 41)]
LONG_NEW = list(LONG_OLD)
LONG_NEW[4] = "line 5 changed"
LONG_NEW[30] = "line 31 changed"
LONG_DIFF = "".join(
    difflib.unified_diff(
        [line + "\n" for line in LONG_OLD], [line + "\n" for line in LONG_NEW], n=3
    )
)
LONG_PAGES = [Page(path="long.py", diff=LONG_DIFF)]


def _long_request(files):
    return ReviewRequest(pages=LONG_PAGES, files=files)


def _long_comments(request, *entries):
    data = {"comments": [{"path": "long.py", "text": "t", **entry} for entry in entries]}
    submission = Submission(kind="submit", data=data)
    return _result_from_submission(request.pages, submission, None, request.expansion())["comments"]


def test_a_hidden_line_is_not_addressable_without_the_file():
    request = _long_request({})
    assert _long_comments(request, {"side": "new", "line": 15}) == []


def test_a_hidden_line_can_be_commented_once_the_page_could_expand_it():
    request = _long_request({"long.py": "\n".join(LONG_NEW) + "\n"})
    [comment] = _long_comments(request, {"side": "new", "line": 15})
    assert comment["context"] == "line 15"


def test_a_range_across_expanded_lines_includes_every_row_between_its_ends():
    request = _long_request({"long.py": "\n".join(LONG_NEW) + "\n"})
    [comment] = _long_comments(
        request, {"side": "new", "line": 8, "end_side": "new", "end_line": 11}
    )
    assert comment["context"] == "\n".join(f" line {k}" for k in range(8, 12))


def test_hidden_lines_keep_their_old_numbers_after_a_shift():
    old = [f"line {k}" for k in range(1, 21)]
    new = ["inserted", *old]  # everything below moves down by one
    diff = "".join(difflib.unified_diff([x + "\n" for x in old], [x + "\n" for x in new], n=1))
    page = Page(path="s.py", diff=diff)
    request = ReviewRequest(pages=[page], files={"s.py": "\n".join(new) + "\n"})
    data = {"comments": [{"path": "s.py", "side": "new", "line": 15, "text": "t"}]}
    [comment] = _result_from_submission(
        request.pages, Submission(kind="submit", data=data), None, request.expansion()
    )["comments"]
    assert comment["context"] == "line 14"  # new 15 is old 14


def test_a_file_that_does_not_match_its_diff_expands_nothing():
    request = _long_request({"long.py": "completely different\n"})
    assert request.expansion() == {}

from __future__ import annotations

from pretty_prompt.listener import Submission
from pretty_prompt.review import Page, _result_from_submission

DIFF = "@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"
PAGES = [Page(path="a.py", diff=DIFF)]
COMMENT = {"path": "a.py", "side": "new", "line": 2, "text": " why? "}


def test_submit_returns_comments_with_their_context():
    submission = Submission(kind="submit", data={"comments": [COMMENT], "overall_note": " ok "})
    assert _result_from_submission(PAGES, submission, None) == {
        "comments": [{**COMMENT, "text": "why?", "context": "new"}],
        "overall_note": "ok",
    }


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
        "overall_note": None,
        "abandoned": True,
    }


def test_abandoned_progress_with_an_unknown_line_is_dropped():
    progress = {"comments": [{**COMMENT, "line": 99}], "overall_note": ""}
    assert _result_from_submission(PAGES, None, progress)["comments"] == []

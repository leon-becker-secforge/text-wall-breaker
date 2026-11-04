from pretty_prompt.diff import parse_unified_diff
from pretty_prompt.highlight import highlight_rows

DIFF = (
    "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n def f():\n-    return 1\n+    return 'a'  # c\n \n"
)


def test_rows_get_token_spans_keyed_by_row_index():
    rows = parse_unified_diff(DIFF)
    colored = highlight_rows("x.py", rows)
    assert 0 not in colored  # the hunk header
    assert '<span class="tk-k">def</span>' in colored[1]
    assert '<span class="tk-mi">1</span>' in colored[2]
    assert '<span class="tk-c1"># c</span>' in colored[3]


def test_a_side_not_ending_in_a_blank_line_is_still_colored():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+import os\n+x = 1\n"
    colored = highlight_rows("x.py", parse_unified_diff(diff))
    assert '<span class="tk-kn">import</span>' in colored[1]
    assert '<span class="tk-mi">1</span>' in colored[2]


def test_a_file_type_without_a_lexer_stays_plain():
    assert highlight_rows("notes.unknownext", parse_unified_diff(DIFF)) == {}


def test_markup_in_the_source_is_escaped():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+x = '<script>'\n"
    colored = highlight_rows("x.py", parse_unified_diff(diff))
    assert "<script>" not in colored[1]
    assert "&lt;script&gt;" in colored[1]

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pretty_prompt.render import is_renderable, rendered_view

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "9c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def _view(text: str, path: str = "docs/a.md", **kwargs) -> str:
    html = rendered_view(path, text.split("\n"), **kwargs)
    assert html is not None
    return html


def _ranges(html: str) -> list[tuple[str, int, int]]:
    return [
        (m[1], int(m[2]), int(m[3]))
        for m in re.finditer(r'<(\w+)[^>]*data-first="(\d+)" data-last="(\d+)"', html)
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.md", True),
        ("A.MD", True),
        ("x/y.markdown", True),
        ("a.adoc", True),
        ("a.asciidoc", True),
        ("a.txt", False),
        ("md", False),
        ("a.md.bak", False),
    ],
)
def test_which_files_have_a_rendered_view(path, expected):
    assert is_renderable(path) is expected


def test_a_file_of_another_kind_has_none():
    assert rendered_view("a.py", ["x = 1"]) is None


def test_every_block_carries_the_source_lines_it_stands_for():
    html = _view("# T\n\npara one\nstill one\n\n- a\n- b\n\n```\ncode\n```\n")
    assert _ranges(html) == [
        ("h1", 1, 1),
        ("p", 3, 4),
        ("ul", 6, 7),
        ("li", 6, 6),
        ("li", 7, 7),
        ("code", 9, 11),
    ]


def test_trailing_blank_lines_do_not_belong_to_a_list_item():
    html = _view("1. one\n2. two\n\n3. three\n")
    assert [(tag, a, z) for tag, a, z in _ranges(html) if tag == "li"] == [
        ("li", 1, 1),
        ("li", 2, 2),
        ("li", 4, 4),
    ]


def test_a_table_and_its_rows_are_addressable():
    html = _view("| A | B |\n| - | - |\n| 1 | 2 |\n")
    assert [r for r in _ranges(html) if r[0] in ("table", "tr")] == [
        ("table", 1, 3),
        ("tr", 1, 1),
        ("tr", 3, 3),
    ]


def test_raw_html_is_escaped():
    html = _view('hello <script>alert(1)</script> <b onclick="x()">b</b>')
    assert "<script>" not in html
    assert "<b " not in html
    assert "&lt;script&gt;" in html


def test_a_remote_image_is_replaced_by_its_alt_text():
    html = _view("![the logo](https://example.com/x.png)")
    assert "<img" not in html
    assert "example.com" not in html
    assert "the logo" in html


def test_a_link_opens_in_a_new_tab_and_only_a_web_link_stays_a_link():
    html = _view("[a](https://x.y) [b](other.md) [c](javascript:alert(1))")
    assert 'href="https://x.y" target="_blank" rel="noopener noreferrer"' in html
    assert "other.md" not in html
    assert 'href="javascript' not in html  # refused as a link, left as plain text


def test_a_local_image_inside_the_repo_is_shown(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "pic.png").write_bytes(PNG)
    html = _view("![p](pic.png)", root=str(tmp_path))
    assert f'src="file://{tmp_path.resolve()}/docs/pic.png"' in html


def test_a_local_image_goes_through_the_asset_route_when_the_page_is_served(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "pic.png").write_bytes(PNG)
    html = _view("![p](pic.png)", root=str(tmp_path), asset_base_url="http://127.0.0.1:1/asset?t=x")
    assert 'src="http://127.0.0.1:1/asset?t=x&amp;path=' in html


def test_a_local_image_outside_the_repo_is_not(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(PNG)
    link = repo / "docs" / "link.png"
    link.symlink_to(tmp_path / "secret.png")
    for src in ("../../secret.png", "link.png", "/etc/hostname", "~/x.png", "missing.png"):
        html = _view(f"![p]({src})", root=str(repo))
        assert "<img" not in html


_RUBY = shutil.which("ruby")
needs_asciidoctor = pytest.mark.skipif(
    _RUBY is None
    or subprocess.run([_RUBY, "-e", "require 'asciidoctor'"], capture_output=True).returncode != 0,  # noqa: S603
    reason="Asciidoctor is not installed",
)

ADOC = """= Doc Title

Intro para
second line.

== Section

* one
* two
** nested

.A title
[source,python]
----
x = 1
----

|===
|A|B
|1|2
|===

NOTE: careful <script>alert(1)</script>

image::http://evil/x.png[the logo]
"""


@needs_asciidoctor
def test_an_asciidoc_block_carries_the_lines_it_stands_for():
    html = _view(ADOC, "docs/a.adoc")
    ranges = _ranges(html)
    assert ("p", 3, 4) not in ranges  # the paragraph's wrapper carries it
    assert ("div", 3, 4) in ranges
    assert ("h2", 6, 6) in ranges
    assert ("li", 8, 8) in ranges
    assert ("li", 9, 10) in ranges  # an item runs to its nested list
    assert ("div", 12, 16) in ranges  # title, attributes and delimiters belong to the listing
    assert ("table", 18, 21) in ranges


@needs_asciidoctor
def test_asciidoc_output_is_cleaned():
    html = _view(ADOC, "docs/a.adoc")
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    assert "the logo" in html


MERMAID_MD = "# T\n\n```mermaid\ngraph TD\n  A-->B\n```\n\n```python\nprint(1)\n```"


def test_a_mermaid_fence_in_markdown_is_a_diagram_block_with_its_lines():
    html = _view(MERMAID_MD)
    assert '<pre data-first="3" data-last="6" class="mermaid">graph TD\n  A--&gt;B\n</pre>' in html
    # another language stays a code block
    assert 'class="language-python"' in html and html.count('class="mermaid"') == 1


MERMAID_ADOC = (
    "= T\n\n[mermaid]\n----\ngraph TD\n  A-->B\n----\n\n[source,mermaid]\n----\ngraph TD\n"
    "  C-->D\n----\n\n[source,python]\n----\nprint(1)\n----"
)


@needs_asciidoctor
def test_both_asciidoc_mermaid_forms_are_diagram_blocks_with_their_lines():
    html = _view(MERMAID_ADOC, "docs/a.adoc")
    assert html.count('<pre class="mermaid"') == 2
    assert 'class="mermaid" data-first="3" data-last="7">graph TD\n  A--&gt;B</pre>' in html
    assert 'class="mermaid" data-first="9" data-last="13">graph TD\n  C--&gt;D</pre>' in html
    assert 'class="language-python"' in html


def test_asciidoc_is_left_out_without_ruby(monkeypatch):
    monkeypatch.setattr("pretty_prompt.render.shutil.which", lambda _name: None)
    assert rendered_view("a.adoc", ["= T"]) is None


def test_without_a_repo_no_local_image_is_shown(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "pic.png").write_bytes(PNG)
    assert "<img" not in _view("![p](pic.png)")

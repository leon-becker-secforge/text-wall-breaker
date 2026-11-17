from __future__ import annotations

from pathlib import Path

import pytest

from text_wall_breaker import glossary
from text_wall_breaker.page import build_multi_page, build_page

SAMPLE = """# Title

## Language

### Asking

**Option**:
One choice offered for a question,
identified by an id the operator never sees.
_Avoid_: choice, answer

**Page** (review):
One file's diff.
_Avoid_: slide
"""


def test_parse_reads_terms_and_joins_wrapped_definitions():
    assert glossary.parse(SAMPLE) == [
        {
            "term": "Option",
            "definition": "One choice offered for a question, identified by an id the "
            "operator never sees.",
        },
        {"term": "Page", "definition": "One file's diff."},
    ]


def test_find_file_prefers_the_nearest_folder_and_glossary_over_context(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "CONTEXT.md").write_text("root")
    sub = tmp_path / "lib" / "src"
    sub.mkdir(parents=True)
    assert glossary.find_file(sub) == tmp_path / "CONTEXT.md"
    (tmp_path / "lib" / "CONTEXT.md").write_text("lib")
    assert glossary.find_file(sub) == tmp_path / "lib" / "CONTEXT.md"
    (tmp_path / "lib" / "GLOSSARY.md").write_text("lib glossary")
    assert glossary.find_file(sub) == tmp_path / "lib" / "GLOSSARY.md"


def test_find_file_stops_at_the_git_root(tmp_path: Path):
    (tmp_path / "CONTEXT.md").write_text("outside")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert glossary.find_file(repo) is None


def test_load_returns_none_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "CONTEXT.md").write_text(SAMPLE)
    assert glossary.load(str(tmp_path)) is not None
    monkeypatch.setenv(glossary.DISABLE_ENV_VAR, "off")
    assert glossary.load(str(tmp_path)) is None


def test_load_returns_none_without_a_glossary(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    assert glossary.load(str(tmp_path)) is None


ENTRIES = [{"term": "Option", "definition": "One choice."}]


def test_pages_embed_the_glossary_only_when_given_one():
    single = build_page(
        question="q", options=[], multi_select=False, allow_free_text=True, submit_url="u"
    )
    assert "__PP_GLOSSARY__" not in single
    with_glossary = build_page(
        question="q",
        options=[],
        multi_select=False,
        allow_free_text=True,
        submit_url="u",
        glossary=ENTRIES,
    )
    assert "__PP_GLOSSARY__" in with_glossary and "One choice." in with_glossary
    multi = build_multi_page(
        questions=[
            {"question": "q", "options": [], "multi_select": False, "allow_free_text": True}
        ],
        submit_url="u",
        glossary=ENTRIES,
    )
    assert "__PP_GLOSSARY__" in multi

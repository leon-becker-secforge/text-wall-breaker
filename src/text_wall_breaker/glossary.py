"""Find and parse the project's glossary, so the ask page can define its terms on hover.

The glossary is `GLOSSARY.md`, or `CONTEXT.md` where there is none: the file format is the
one the `domain-modeling` skill writes (`**Term**:` followed by its definition, then an
`_Avoid_:` line).
"""

from __future__ import annotations

import re
from pathlib import Path

FILE_NAMES = ("GLOSSARY.md", "CONTEXT.md")

_TERM_LINE = re.compile(r"^\*\*(?P<term>[^*]+)\*\*(?:\s*\([^)]*\))?:\s*(?P<rest>.*)$")
_MAX_FILE_BYTES = 200_000
_MAX_DEFINITION_CHARS = 400


def parse(text: str) -> list[dict[str, str]]:
    """The `{"term", "definition"}` entries of a glossary, in file order."""
    entries: list[dict[str, str]] = []
    term: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if term is not None and lines:
            definition = " ".join(lines).strip()
            if len(definition) > _MAX_DEFINITION_CHARS:
                definition = definition[: _MAX_DEFINITION_CHARS - 1].rstrip() + "…"
            entries.append({"term": term, "definition": definition})

    for line in text.splitlines():
        match = _TERM_LINE.match(line.strip())
        if match:
            flush()
            term = match["term"].strip()
            lines = [match["rest"].strip()] if match["rest"].strip() else []
        elif term is None:
            continue
        elif not line.strip() or line.startswith("#") or line.lstrip().startswith("_Avoid_"):
            flush()
            term, lines = None, []
        else:
            lines.append(line.strip())
    flush()
    return entries


def find_file(start: Path) -> Path | None:
    """The nearest glossary file from `start` up to the enclosing git root, or None.

    Nearest wins, so a subfolder's own glossary beats the repository root's, which is
    how a repo with several contexts picks the one for the folder it is working in.
    """
    for folder in (start, *start.parents):
        for name in FILE_NAMES:
            candidate = folder / name
            if candidate.is_file():
                return candidate
        if (folder / ".git").exists():
            return None
    return None


def load(project: str | None = None) -> list[dict[str, str]] | None:
    """The glossary entries for `project` (a folder), or the server's cwd; None if none.

    Never raises: a glossary is a convenience, and an unreadable one must not fail an ask.
    """
    try:
        start = Path(project).expanduser().resolve() if project else Path.cwd()
        path = find_file(start)
        if path is None or path.stat().st_size > _MAX_FILE_BYTES:
            return None
        entries = parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return entries or None

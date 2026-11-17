#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "typer",
# ]
# ///
"""Print one version's section from a Keep a Changelog-style CHANGELOG.md.

A section starts at a line matching "## [<version>]" and runs to the next "## "
heading, or the end of the file. Exits 1 with a message on stderr if the file has no
such section — the release workflow uses that to fail loudly on a forgotten changelog
entry rather than publish a release with empty notes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


def extract(text: str, version: str) -> str | None:
    heading = re.compile(r"^## \[" + re.escape(version) + r"\].*$", re.MULTILINE)
    match = heading.search(text)
    if match is None:
        return None
    start = match.end()
    next_heading = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip() + "\n"


@app.command()
def main(
    changelog: Annotated[Path, typer.Argument(help="Path to the CHANGELOG.md to read.")],
    version: Annotated[str, typer.Argument(help="Version to extract, without a 'v' prefix.")],
) -> None:
    text = changelog.read_text(encoding="utf-8")
    section = extract(text, version)
    if section is None:
        print(f"::error::No '## [{version}]' section found in {changelog}", file=sys.stderr)
        raise typer.Exit(1)
    sys.stdout.write(section)


if __name__ == "__main__":
    app()

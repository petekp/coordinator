"""
Where a domain's notes are. The notes file is <notes_dir>/<domain>-notes.md,
or <domain>-backlog.md, the older name, when there is no file by the new
name. A second file, <domain>-later.md beside it, holds Later and Done once
the notes file has no Done section of its own.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple, Optional

from check_error import CheckError
from config import CONFIG

NOTES_DIR = Path(CONFIG["notes_dir"]).expanduser()
NOTES_SUFFIX = "-notes.md"
OLDER_NOTES_SUFFIX = "-backlog.md"
LATER_SUFFIX = "-later.md"
SECTION_HEADING = re.compile(r"^## (?!#)")


def sections_named(text: str, heading: str) -> str:
    """The text of every section with that heading, one after another."""
    bodies: list[str] = []
    body: list[str] | None = None
    for line in text.splitlines():
        if SECTION_HEADING.match(line):
            if body is not None:
                bodies.append("\n".join(body))
            body = [] if line.rstrip() == heading else None
        elif body is not None:
            body.append(line)
    if body is not None:
        bodies.append("\n".join(body))
    return "\n".join(bodies)


def joined(notes_text: str, later_text: str) -> str:
    """The notes file and its second file as one text. Without a second file, the notes file's text as it is."""
    if not later_text:
        return notes_text
    return notes_text.rstrip("\n") + "\n\n" + later_text


class DomainFiles(NamedTuple):
    """A domain's notes file; the second file beside it, which may not exist; and a file under the
    other name that is not the notes file itself, which the scripts do not read."""
    domain: str
    notes: Path
    later: Path
    stray: Optional[Path]

    def later_text(self) -> str:
        return self.later.read_text() if self.later.is_file() else ""

    def text(self) -> str:
        """Both files as one text."""
        return joined(self.notes.read_text(), self.later_text())


def domain_files(domain: str, folder: Path = NOTES_DIR) -> DomainFiles:
    """<domain>-notes.md, else <domain>-backlog.md, and <domain>-later.md beside it. A link at one name to
    the other is the same file, not a stray."""
    notes = folder / f"{domain}{NOTES_SUFFIX}"
    older = folder / f"{domain}{OLDER_NOTES_SUFFIX}"
    stray = None
    if notes.is_file():
        if older.is_file() and not os.path.samefile(notes, older):
            stray = older
    else:
        notes = older
    return DomainFiles(domain, notes, folder / f"{domain}{LATER_SUFFIX}", stray)


def domains_in(folder: Path = NOTES_DIR) -> list[str]:
    """Each domain with a notes file under either name, once."""
    found = set()
    for suffix in (NOTES_SUFFIX, OLDER_NOTES_SUFFIX):
        found |= {path.name[:-len(suffix)] for path in folder.glob(f"*{suffix}") if path.is_file()}
    return sorted(found)


def open_section(backlog: Path) -> str:
    if not backlog.is_file():
        raise CheckError(f"no backlog at {backlog}")
    body: list[str] | None = None
    in_fence = False
    for line in backlog.read_text().splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        is_heading = not in_fence and SECTION_HEADING.match(line)
        if body is None:
            if is_heading and line.rstrip() == "## Open":
                body = []
        elif is_heading:
            break
        else:
            body.append(line)
    if body is None:
        raise CheckError(f"{backlog} has no '## Open' section")
    return "\n".join(body)


def section_text(text: str, heading: str) -> str:
    body: list[str] | None = None
    for line in text.splitlines():
        if body is None:
            if line.rstrip() == heading:
                body = []
        elif SECTION_HEADING.match(line):
            break
        else:
            body.append(line)
    return "\n".join(body or [])


def open_and_later(files: DomainFiles) -> str:
    """The notes file's Open section, then any Later section in either file under its heading."""
    open_items = open_section(files.notes)
    later = sections_named(files.text(), "## Later")
    return f"{open_items}\n## Later\n{later}" if later.strip() else open_items

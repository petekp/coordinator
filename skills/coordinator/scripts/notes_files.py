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
DONE_HEADING = re.compile(r"^## Done(?![\w-])")
FENCE = re.compile(r"^\s*(?:```|~~~)")


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


def has_domain_shape(path: Path) -> bool:
    """The file has an ## Open section outside code fences, as every domain's notes do."""
    try:
        lines = path.read_text().splitlines() if path.is_file() else []
    except (OSError, UnicodeDecodeError):
        return False
    return section_span(lines, lambda heading: heading == "## Open") is not None


def domain_files(domain: str, folder: Path = NOTES_DIR) -> DomainFiles:
    """<domain>-notes.md, else <domain>-backlog.md, and <domain>-later.md beside it. A file under the new
    name that is not a domain's notes, such as release-notes.md, gives way to one under the older name
    that is. A link at one name to the other is the same file, not a stray."""
    newer = folder / f"{domain}{NOTES_SUFFIX}"
    older = folder / f"{domain}{OLDER_NOTES_SUFFIX}"
    older_is_notes = has_domain_shape(older)
    use_newer = newer.is_file() and (has_domain_shape(newer) or not older_is_notes)
    stray = older if use_newer and older_is_notes and not os.path.samefile(newer, older) else None
    return DomainFiles(domain, newer if use_newer else older, folder / f"{domain}{LATER_SUFFIX}", stray)


def domains_in(folder: Path = NOTES_DIR) -> list[str]:
    """Each domain with a notes file under either name, once. A file with no ## Open section is not one."""
    found = set()
    for suffix in (NOTES_SUFFIX, OLDER_NOTES_SUFFIX):
        found |= {path.name[:-len(suffix)] for path in folder.glob(f"*{suffix}") if has_domain_shape(path)}
    return sorted(found)


def why_not_a_domain(domain: str, folder: Path = NOTES_DIR) -> CheckError:
    """Why the domain has no notes: its file has no ## Open section, or it has no file."""
    try:
        open_section(domain_files(domain, folder).notes)
    except CheckError as error:
        return error
    return CheckError(f"no backlog for {domain} in {folder}")


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


def section_span(lines: list[str], wanted) -> tuple[int, int] | None:
    """Where the body of the first section whose heading line passes wanted starts and ends in lines.
    A heading inside a code fence is text, not a heading."""
    start = None
    in_fence = False
    for number, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
        if in_fence or not SECTION_HEADING.match(line):
            continue
        if start is not None:
            return start, number
        if wanted(line.rstrip()):
            start = number + 1
    return (start, len(lines)) if start is not None else None


def section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    span = section_span(lines, lambda line: line == heading)
    return "\n".join(lines[span[0]:span[1]]) if span else ""


def done_text(text: str) -> str:
    """The Done section, whose heading may carry more words, as in "## Done (newest first)"."""
    lines = text.splitlines()
    span = section_span(lines, DONE_HEADING.match)
    return "\n".join(lines[span[0]:span[1]]) if span else ""


def open_and_later(files: DomainFiles) -> str:
    """The notes file's Open section, then any Later section in either file under its heading."""
    open_items = open_section(files.notes)
    later = sections_named(files.text(), "## Later")
    return f"{open_items}\n## Later\n{later}" if later.strip() else open_items

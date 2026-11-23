"""
A domain's notes: their shape, their size limits, their items, and the open
pull requests they name.

A notes file in one piece holds Boundary, Standing yes, Open and Done. The
coordinator reads everything outside Done at each start and after each
compaction, so that part has a limit, 40 KB, and Done has its own, 20 KB.

A notes file in two files has no Done section. Its Later and Done sections
are in <domain>-later.md beside it, which the coordinator searches and does
not read whole. The coordinator reads the notes file whole, so the whole
file has the limit, 20 KB. Done's limit is 100 KB and Later's is 40 KB.
Only the notes file decides its shape: a second file that appears while the
notes file still has a Done section leaves it a file in one piece.

notes_limit_kb in the config, or its older name backlog_limit_kb, sets the
first limit for either shape, and done_limit_kb sets Done's. A notes file
whose part read at each start is over trim_by_delegate_over_kb is too large
for its coordinator to read whole, so one delegate trims it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

import fleet
import notes_files
from fleet import CONFIG

BYTES_PER_KB = 1000
ONE_PIECE_LIMITS_KB = (40, 20)
TWO_FILE_LIMITS_KB = (20, 100)
LIMIT_OF_LATER = 40 * BYTES_PER_KB
TRIMMED_BY_A_DELEGATE_OVER = CONFIG["trim_by_delegate_over_kb"] * BYTES_PER_KB
TRIM_STEPS = Path(fleet.SCRIPTS).parent / "backlog-trim.md"
TITLE_CHARS = 160
LARGEST_OPEN_ITEMS_NAMED = 3
LARGEST_ITEM_TITLE_CHARS = 80
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s")
BLOCK_HEADING = re.compile(r"^### ")
FENCE = re.compile(r"^\s*(?:```|~~~)")
HOLDS_AN_ASK = re.compile(r"(?<!\w)Ask:")
MARKDOWN_LINK = re.compile(r"\[[^\]\n]*\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
HOME_PATH = re.compile(r"(?<![\w.:/~@%+<>}-])~/[\w.@%+-]+(?:/[\w.@%+-]*)*")
ABSOLUTE_PATH = re.compile(r"(?<![\w.:/~@%+<>}-])/[\w.@%+-]+(?:/[\w.@%+-]+)+/?")
URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
TEMPLATE_AFTER_A_PATH = tuple("<{*$[")


def sections(text: str) -> dict[str, str]:
    """Each `## ` section's text by its heading, in file order. Text before the first heading is under ""."""
    found: dict[str, list[str]] = {"": []}
    heading = ""
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and notes_files.SECTION_HEADING.match(line):
            heading = line.rstrip()
            found.setdefault(heading, [])
        else:
            found[heading].append(line)
    return {heading: "\n".join(lines) for heading, lines in found.items()}


def size(text: str) -> int:
    return len(text.encode())


def kb(size_in_bytes: int) -> str:
    return f"{size_in_bytes / BYTES_PER_KB:.1f} KB"


def bytes_outside_done(text: str) -> int:
    return size(text) - size(sections(text).get("## Done", ""))


def in_two_files(notes_text: str) -> bool:
    """The notes file has no Done section, so Later and Done live in the second file."""
    return "## Done" not in sections(notes_text)


def limits(two_files: bool, config: dict = CONFIG) -> tuple[int, int]:
    """In bytes: the limit of what a coordinator reads at each start, and Done's."""
    read_at_start_kb, done_kb = TWO_FILE_LIMITS_KB if two_files else ONE_PIECE_LIMITS_KB
    if config["notes_limit_kb"] is not None:
        read_at_start_kb = config["notes_limit_kb"]
    if config["done_limit_kb"] is not None:
        done_kb = config["done_limit_kb"]
    return read_at_start_kb * BYTES_PER_KB, done_kb * BYTES_PER_KB


class Item(NamedTuple):
    line: int
    text: str

    @property
    def title(self) -> str:
        """The item's first line, without its list marker or heading mark and with bold marks removed."""
        first = next(line for line in self.text.splitlines() if line.strip())
        first = LIST_ITEM.sub("", BLOCK_HEADING.sub("", first), count=1)
        return cut(first.replace("**", "").strip(), TITLE_CHARS)


def cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[:chars - 1] + "…"


def items(section: str, first_line: int = 0) -> list[Item]:
    """Each top-level list item, paragraph or `###` block of a section, with the number of its first line
    counted from first_line. A `###` block runs to the next one and holds the list items under it."""
    found: list[tuple[int, list[str]]] = []
    in_block = in_fence = False
    after_blank = True
    for number, line in enumerate(section.splitlines(), start=first_line):
        is_fence = bool(FENCE.match(line))
        if in_fence or is_fence:
            in_fence = in_fence != is_fence
            if found:
                found[-1][1].append(line)
            else:
                found.append((number, [line]))
            after_blank = False
            continue
        if not line.strip():
            after_blank = True
            if found:
                found[-1][1].append(line)
            continue
        if BLOCK_HEADING.match(line):
            in_block = True
            found.append((number, [line]))
        elif not in_block and (LIST_ITEM.match(line) or (after_blank and not line[0].isspace())):
            found.append((number, [line]))
        elif found:
            found[-1][1].append(line)
        else:
            found.append((number, [line]))
        after_blank = False
    return [Item(number, "\n".join(lines).rstrip()) for number, lines in found]


def line_of_heading(text: str, heading: str) -> int:
    """The 1-based number of the heading's line in the text, or 0 when it has none."""
    return next((number for number, line in enumerate(text.splitlines(), start=1) if line.rstrip() == heading), 0)


class Notes:
    """A domain's notes file and second file, read once."""

    def __init__(self, files: notes_files.DomainFiles, notes_text: str | None = None, later_text: str | None = None):
        self.files = files
        self.text = files.notes.read_text() if notes_text is None else notes_text
        self.later_text = files.later_text() if later_text is None else later_text
        self.two_files = in_two_files(self.text)
        self.read_at_start_limit, self.done_limit = limits(self.two_files)

    @property
    def whole(self) -> str:
        return notes_files.joined(self.text, self.later_text)

    @property
    def second_file_ignored(self) -> bool:
        """A second file exists, but the notes file still has its own Done section."""
        return not self.two_files and self.files.later.is_file()

    def read_at_start(self) -> int:
        return size(self.text) if self.two_files else bytes_outside_done(self.text)

    def done(self) -> str:
        return sections(self.later_text if self.two_files else self.text).get("## Done", "")

    def later(self) -> str:
        return sections(self.later_text).get("## Later", "") if self.two_files else ""

    def later_items(self) -> list[Item]:
        return items(self.later(), line_of_heading(self.later_text, "## Later") + 1)

    def largest_open_items(self) -> list[Item]:
        found = items(sections(self.text).get("## Open", ""))
        return sorted(found, key=lambda item: -size(item.text))[:LARGEST_OPEN_ITEMS_NAMED]

    def over_their_limit(self) -> list[tuple[str, str]]:
        """For each part over its size limit, a phrase for it and what to do."""
        over = []
        read_at_start = self.read_at_start()
        if read_at_start > self.read_at_start_limit:
            part = "the notes file" if self.two_files else "everything outside Done"
            over.append((f"{part} is {kb(read_at_start)}, limit {self.read_at_start_limit // BYTES_PER_KB}",
                         self.what_to_do_when_read_at_start_is_over()))
        done = size(self.done())
        if done > self.done_limit:
            over.append((f"Done is {kb(done)}, limit {self.done_limit // BYTES_PER_KB}", self.what_to_do_when_done_is_over()))
        later = size(self.later())
        if later > LIMIT_OF_LATER:
            over.append((f"Later is {kb(later)}, limit {LIMIT_OF_LATER // BYTES_PER_KB}",
                         "Bring the user the oldest Later items as one question: keep or drop each."))
        return over

    def what_to_do_when_read_at_start_is_over(self) -> str:
        if self.read_at_start() > TRIMMED_BY_A_DELEGATE_OVER:
            return (f"Start one delegate to trim it as {TRIM_STEPS} says, because reading it would fill your "
                    "context. Read that file first.")
        if not self.two_files:
            return f"Trim it as {TRIM_STEPS} says; read that file first."
        largest = "; ".join(f"\"{cut(item.title, LARGEST_ITEM_TITLE_CHARS)}\" {kb(size(item.text))}"
                            for item in self.largest_open_items())
        return (f"Its largest Open items: {largest}. Move each item with nothing in flight to the Later section of "
                f"{self.files.later}, with a first line that names what wakes it, and move decisions to the "
                f"project's own record, leaving a link. Then run {fleet.SCRIPTS}/backlog_check.py with --since.")

    def what_to_do_when_done_is_over(self) -> str:
        return (f"Run {fleet.SCRIPTS}/backlog_check.py {self.files.domain} --cut-done, which deletes the oldest lines of Done "
                "until it is at most half its limit.")

    def standing_rules(self) -> list[str]:
        """The watcher's rules for these notes: each part over its limit, and a stray file."""
        where = "in your notes" if self.two_files else "in your backlog"
        rules = [f"{where}, {part} · {what_to_do}" for part, what_to_do in self.over_their_limit()]
        return rules + ([self.stray_warning()] if self.files.stray else [])

    def stray_warning(self) -> str:
        stray, notes = self.files.stray, self.files.notes
        return (f"{stray} is a separate file from {notes}, and the scripts read only {notes.name}. Move what "
                f"{stray.name} holds that {notes.name} lacks into {notes.name}, then delete {stray.name}.")

    def dead_links(self) -> list[tuple[str, str]]:
        """Each local path outside Done that no longer exists, with the file and section it is in, once
        each. Paths in code blocks, and ones followed by a placeholder such as <name>, are skipped. A relative
        Markdown link is read from the folder of the file it is in."""
        found: dict[str, tuple[str, str]] = {}
        texts = [(self.files.notes, self.text)] + ([(self.files.later, self.later_text)] if self.later_text else [])
        for path, text in texts:
            for heading, body in sections(text).items():
                if heading == "## Done":
                    continue
                for written in local_paths(body):
                    target = os.path.expanduser(written) if written.startswith(("~", "/")) else str(path.parent / written)
                    if written not in found and not os.path.exists(target):
                        found[written] = (path.name, heading or "the top")
        missing_later = self.two_files and not self.files.later.is_file()
        if missing_later and str(self.files.later) not in found:
            found[str(self.files.later)] = (self.files.notes.name, "its second file, which the notes file needs once "
                                            "it has no Done section")
        return [(written, f"{name}, {where}") for written, (name, where) in found.items()]


def local_paths(text: str) -> list[str]:
    """Local paths outside code blocks: `~/` and absolute paths of two or more parts, and Markdown link
    targets with no URL scheme. A code span that holds only a path is read whole, so it may hold spaces."""
    paths = []

    def whole_span_path(match: re.Match) -> str:
        span = match.group(1).strip()
        is_path = (HOME_PATH.fullmatch(span.replace(" ", "_")) or ABSOLUTE_PATH.fullmatch(span.replace(" ", "_")))
        if is_path and not any(character in span for character in TEMPLATE_AFTER_A_PATH):
            paths.append(span)
            return " "
        return match.group(0)

    plain = CODE_SPAN.sub(whole_span_path, "\n".join(without_code_blocks(text)))
    for match in MARKDOWN_LINK.finditer(plain):
        target = match.group(1).split("#", 1)[0]
        if target and not URL_SCHEME.match(target):
            paths.append(target)
    for pattern in (HOME_PATH, ABSOLUTE_PATH):
        for match in pattern.finditer(plain):
            next_character = plain[match.end():match.end() + 1]
            if next_character and next_character in TEMPLATE_AFTER_A_PATH:
                continue
            path = match.group(0).rstrip(".,;:")
            if path.rstrip("/") not in ("~", ""):
                paths.append(path)
    return list(dict.fromkeys(paths))


def without_code_blocks(text: str) -> list[str]:
    lines, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def parts_over_their_limit(notes: Notes) -> list[str]:
    """One phrase for each part of a domain's notes that is over its size limit."""
    return [part for part, _ in notes.over_their_limit()]


def pull_requests_named(text: str, open_numbers: dict[str, list[int]]) -> set[tuple[str, int]]:
    """The open pull requests the text names, by fleet_status.py's rule: a bare #<number> counts only
    when no other of these repos has that number open."""
    every_number = [number for numbers in open_numbers.values() for number in numbers]
    return {(slug, number) for slug, numbers in open_numbers.items() for number in numbers
            if fleet.names_pull_request(text, slug, number, every_number.count(number) > 1)}

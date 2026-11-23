"""
A domain's notes: their shape, their size limits, their items, and the open
pull requests they name.

A notes file in one piece holds Boundary, Standing yes, Open and Done. The
coordinator reads everything outside Done at each start and after each
compaction, so that part has a limit, 40 KB, and Done has its own, 20 KB.

A notes file in two files has no Done section, and its Later and Done
sections are in <domain>-later.md beside it, which the coordinator searches and does
not read whole. The coordinator reads the notes file whole, so the whole
file has the limit, 20 KB. Done's limit is 100 KB. Later has no size limit:
the watcher asks for a review of the items that --resume hides.
A notes file with a Done section is in one piece even when a second file
exists, and so is one with neither, such as a new domain's. A Done heading
may carry more words, as in "## Done (newest first)".

notes_limit_kb in the config, or its older name backlog_limit_kb, sets the
first limit for either shape, and done_limit_kb sets Done's. A notes file
whose part read at each start is over trim_by_delegate_over_kb is too large
for its coordinator to read whole, so one delegate trims it.
"""

from __future__ import annotations

import itertools
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
LATER_LINES_CHARS = 3000
TRIMMED_BY_A_DELEGATE_OVER = CONFIG["trim_by_delegate_over_kb"] * BYTES_PER_KB
TRIM_STEPS = Path(fleet.SCRIPTS).parent / "backlog-trim.md"
TITLE_CHARS = 160
LARGEST_OPEN_ITEMS_NAMED = 3
LARGEST_ITEM_TITLE_CHARS = 80
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s")
BLOCK_HEADING = re.compile(r"^### ")
FENCE = re.compile(r"^\s*(?:```|~~~)")
HOLDS_AN_ASK = re.compile(r"(?<!\w)Ask:")
HOME_PATH = re.compile(r"(?<![\w.:/~@%+<>}-])~/[\w.@%+-]+(?:/[\w.@%+-]*)*")
ABSOLUTE_PATH = re.compile(r"(?<![\w.:/~@%+<>}-])/[\w.@%+-]+(?:/[\w.@%+-]+)+/?")
WORDS_TRIED_AFTER_A_PATH = 8
CHARACTERS_TRIED_AFTER_A_PATH = 200
ROOTS_THAT_HOLD_NO_NOTES = ("/dev/",)
CODE_SPAN = re.compile(r"`([^`\n]+)`")
PLACEHOLDER_MARKS = tuple("<{*$[…")


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
    return size(text) - size(done_of(text))


def done_of(text: str) -> str:
    return "\n".join(body for heading, body in sections(text).items() if notes_files.DONE_HEADING.match(heading))


def in_two_files(notes_text: str, second_file_exists: bool) -> bool:
    """The notes file has no Done section and the second file exists, so Later and Done live there."""
    has_done = any(notes_files.DONE_HEADING.match(heading) for heading in sections(notes_text))
    return second_file_exists and not has_done


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
        self.two_files = in_two_files(self.text, files.later.is_file())
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
        return done_of(self.later_text if self.two_files else self.text)

    def later(self) -> str:
        return sections(self.later_text).get("## Later", "") if self.two_files else ""

    def later_items(self) -> list[Item]:
        return items(self.later(), line_of_heading(self.later_text, "## Later") + 1)

    def later_hidden_and_shown(self) -> tuple[list[Item], list[Item]]:
        """The Later items that --resume leaves out, oldest first, and those it prints, up to LATER_LINES_CHARS."""
        every = self.later_items()
        shown: list[Item] = []
        chars = 0
        for item in reversed(every):
            chars += len(f"- {item.title}") + 1
            if chars > LATER_LINES_CHARS:
                break
            shown.insert(0, item)
        return every[:len(every) - len(shown)], shown

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
        """Each local path outside Done that no longer exists, with the file and section it is in, once each."""
        found: dict[str, tuple[str, str]] = {}
        texts = [(self.files.notes, self.text)] + ([(self.files.later, self.later_text)] if self.later_text else [])
        for path, text in texts:
            for heading, body in sections(text).items():
                if notes_files.DONE_HEADING.match(heading):
                    continue
                for written, after in local_paths(body):
                    if written not in found and could_be_here(written) and not exists_with_words_after(written, after):
                        found[written] = (path.name, heading or "the top")
        return [(written, f"{name}, {where}") for written, (name, where) in found.items()]


def local_paths(text: str) -> list[tuple[str, str]]:
    """Each `~/` path and absolute path of two or more parts outside code blocks, with the text after it.
    A code span that starts with a path gives the span up to its first word that starts with -, such as a
    flag. A path followed by a placeholder such as <name> or by an ellipsis is skipped, and so is a code span
    that holds one."""
    found = []

    def span_path(match: re.Match) -> str:
        words = match.group(1).strip().split(" ")
        if (HOME_PATH.fullmatch(words[0]) or ABSOLUTE_PATH.fullmatch(words[0])) and not any(
                character in match.group(1) for character in PLACEHOLDER_MARKS):
            found.append((" ".join(itertools.takewhile(lambda word: not word.startswith("-"), words)), ""))
            return " "
        return match.group(0)

    plain = CODE_SPAN.sub(span_path, "\n".join(without_code_blocks(text)))
    for pattern in (HOME_PATH, ABSOLUTE_PATH):
        for match in pattern.finditer(plain):
            after = plain[match.end():match.end() + CHARACTERS_TRIED_AFTER_A_PATH]
            if after[:1] and after[:1] in PLACEHOLDER_MARKS:
                continue
            path = match.group(0).rstrip(".,;:")
            if path.rstrip("/") not in ("~", ""):
                found.append((path, after))
    return found


def could_be_here(written: str) -> bool:
    """A `~/` path, or an absolute path whose top folder exists on this machine. A route such as /sync/v2
    or a path inside a repo such as /src/app.ts starts with a folder the machine does not have."""
    if written.startswith("~"):
        return True
    return os.path.isdir("/" + written.split("/")[1]) and not written.startswith(ROOTS_THAT_HOLD_NO_NOTES)


def exists_with_words_after(written: str, after: str) -> bool:
    """The path, cut after any of its words or after some of the words that follow it, names a file. A
    name can hold spaces, and a line can break inside one."""
    words = written.split(" ") + after.split()[:WORDS_TRIED_AFTER_A_PATH]
    candidates = [" ".join(words[:count]).rstrip(".,;:)`'\"") for count in range(1, len(words) + 1)]
    return any(os.path.exists(os.path.expanduser(candidate)) for candidate in candidates)


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

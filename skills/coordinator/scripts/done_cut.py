"""The oldest items of a Done section, cut until it is at most half its limit."""

from __future__ import annotations

import datetime as dt
import re

import notes_files

A_DATE_NEAR_THE_START = re.compile(r"(?<![\d#-])(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?![\d-])")
CHARACTERS_SEARCHED_FOR_A_DATE = 120
DATE_OF_A_DONE_LINE = re.compile(r"^- (?:\*\*)?(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\b")


def done_cut_to_half(text: str, today: dt.date, limit: int, held: set[str] | None) -> tuple[str, list[str], int, int]:
    """The text with the oldest items of Done deleted until Done is at most half the limit; the lines
    deleted; how many items it kept because the last commit does not hold every line of them; and Done's
    size after the cut. held is the set of lines the last commit holds, or None when Git does not track
    the file, and then any item may go.

    A date with no year is in the twelve months up to tomorrow. A line with no date
    near its start takes the date of the nearest dated line above it.
    """
    tomorrow = today + dt.timedelta(days=1)
    every_line = text.split("\n")
    span = notes_files.section_span(every_line, notes_files.DONE_HEADING.match)
    if span is None:
        return text, [], 0, 0
    lines = every_line[span[0]:span[1]]
    done = "\n".join(lines)
    starts = [i for i, line in enumerate(lines) if line.startswith("- ")]
    items = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        dated = DATE_OF_A_DONE_LINE.match(lines[start]) or A_DATE_NEAR_THE_START.search(lines[start][:CHARACTERS_SEARCHED_FOR_A_DATE])
        written = None
        if dated:
            year, month, day = dated.groups()
            month, day = int(month), int(day)
            if 1 <= month <= 12 and 1 <= day <= 31:
                # A line written late in the day can carry tomorrow's date, in UTC.
                year = int(year) if year else tomorrow.year - ((month, day) > (tomorrow.month, tomorrow.day))
                written = (year, month, day)
        items.append(dict(written=written, start=start, end=end))
    for position, item in enumerate(items):
        if item["written"] is None:
            neighbours = [other["written"] for other in items[:position][::-1] + items[position + 1:] if other["written"]]
            item["written"] = neighbours[0] if neighbours else (0, 0, 0)
    size = len(done.encode())
    target = limit // 2
    deleted: list[int] = []
    kept_uncommitted = 0
    for item in sorted(items, key=lambda item: item["written"]):
        if size <= target:
            break
        item_lines = lines[item["start"]:item["end"]]
        if held is not None and not all(line in held for line in item_lines if line.strip()):
            kept_uncommitted += 1
            continue
        size -= len("\n".join(item_lines).encode()) + 1
        deleted += range(item["start"], item["end"])
    if not deleted:
        return text, [], kept_uncommitted, size
    gone = set(deleted)
    kept = "\n".join(line for i, line in enumerate(lines) if i not in gone).rstrip("\n")
    after = every_line[span[1]:]
    rebuilt = every_line[:span[0]] + [kept, ""] + after
    return "\n".join(rebuilt), [lines[i] for i in sorted(deleted)], kept_uncommitted, size

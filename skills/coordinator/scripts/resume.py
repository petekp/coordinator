"""
What fleet_status.py --resume prints around the status table: the notes a
coordinator reads again after compaction or a restart, and whether the
domain's fleet watcher runs.

For a notes file in one piece it prints Boundary, Standing yes and Open, or
only the sections' sizes when the text outside Done is over its limit.

For a notes file whose Later and Done are in a second file it prints the
whole notes file, with an OVER line first when a part is over its limit.
Then it prints the first line of each Later item, newest last, up to
LATER_LINES_CHARS characters in all, and says how many older items it
leaves out and on which lines they start. Then it prints each Later item
that holds an Ask:, and how many local paths in the two files no longer
exist. Its first line warns when the whole output passes CUT_AT_CHARS.
"""

from __future__ import annotations

from pathlib import Path

import backlog
import fleet
from watch_lock import watcher_running

SECTIONS_READ_AGAIN = ("## Boundary", "## Standing yes", "## Open")
LATER_LINES_CHARS = 3000
CUT_AT_CHARS = 30_000


def warnings_about_the_files(notes: backlog.Notes) -> list[str]:
    files = notes.files
    found = []
    if files.stray:
        found.append(notes.stray_warning())
    if notes.second_file_ignored:
        found.append(f"{files.later} exists, but {files.notes} still has a ## Done section, so the scripts treat "
                     f"{files.notes.name} as a file in one piece and read {files.later.name} only for pull requests, "
                     f"due dates and sessions. Move Later and Done into {files.later.name} to finish the split, or "
                     f"move its items into {files.notes.name}.")
    return found


def one_piece(notes: backlog.Notes) -> str:
    path, text = notes.files.notes, notes.text
    found = backlog.sections(text)
    if notes.read_at_start() > notes.read_at_start_limit:
        sizes = "\n".join(f"{backlog.size(body) / backlog.BYTES_PER_KB:8.1f} KB  {heading}"
                          for heading, body in found.items() if heading)
        return (f"{path} is over its limit, so it is not printed: {'; '.join(backlog.parts_over_their_limit(notes))}.\n"
                f"{sizes}\nRead its Boundary and Standing yes sections yourself, and search Open for each delegate "
                f"and ask you handle. {notes.what_to_do_when_read_at_start_is_over()}")
    return f"{path}\n\n" + "\n\n".join(f"{heading}\n{found[heading].strip()}" for heading in SECTIONS_READ_AGAIN
                                       if heading in found)


def later_lines(notes: backlog.Notes) -> str:
    later = notes.files.later
    every = notes.later_items()
    if not every:
        return f"Later, in {later}: no items."
    shown: list[str] = []
    chars = 0
    for item in reversed(every):
        line = f"- {item.title}"
        if chars + len(line) + 1 > LATER_LINES_CHARS:
            break
        shown.insert(0, line)
        chars += len(line) + 1
    hidden = every[:len(every) - len(shown)]
    out = [f"Later, in {later}: the first line of each item, newest last."]
    if hidden:
        out.append(f"The {len(hidden)} oldest of its {len(every)} items are not shown here. They start on lines "
                   f"{hidden[0].line} to {hidden[-1].line}. Search that file for them.")
    return "\n".join(out + shown)


def later_asks(notes: backlog.Notes) -> str:
    asking = [item.text for item in notes.later_items() if backlog.HOLDS_AN_ASK.search(item.text)]
    if not asking:
        return ""
    return "Later items that hold an Ask:, which belongs in Open:\n" + "\n".join(asking)


def two_files(notes: backlog.Notes) -> str:
    files = notes.files
    parts = [f"OVER  {part}. {what_to_do}" for part, what_to_do in notes.over_their_limit()]
    parts.append(f"{files.notes}\n\n{notes.text.strip()}")
    parts.append(later_lines(notes))
    parts.append(later_asks(notes))
    dead = notes.dead_links()
    if dead:
        parts.append(f"{len(dead)} local paths in {files.notes.name} or {files.later.name} no longer exist; "
                     f"{fleet.SCRIPTS}/backlog_check.py {files.domain} lists them.")
    return "\n\n".join(part for part in parts if part)


def notes_to_read_again(notes: backlog.Notes) -> str:
    body = two_files(notes) if notes.two_files else one_piece(notes)
    return "\n\n".join(warnings_about_the_files(notes) + [body])


def watcher_line(domain: str) -> str:
    state = fleet.WATCH_DIR / f"{domain}.json"
    try:
        pid = watcher_running(state)
    except fleet.CheckError as error:
        return f"Whether a fleet watcher runs for {domain} could not be checked: {error}."
    if pid:
        return f"A fleet watcher runs for {domain}, pid {pid}."
    return (f"No fleet watcher runs for {domain} on {state}. Arm it as the Watch section of "
            f"{Path(fleet.SCRIPTS).parent / 'fleet.md'} says.")


def resume_text(domain: str, status: str) -> str:
    notes = backlog.Notes(fleet.domain_files(domain))
    text = f"{notes_to_read_again(notes)}\n\n{status}\n\n{watcher_line(domain)}"
    if notes.two_files and len(text) > CUT_AT_CHARS:
        text = (f"WARNING  this output is {len(text):,} characters. A tool result over {CUT_AT_CHARS:,} characters "
                f"can lose its middle, which here holds part of {notes.files.notes.name}. Read "
                f"{notes.files.notes} in parts.\n\n{text}")
    return text

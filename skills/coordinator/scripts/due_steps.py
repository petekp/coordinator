"""
Steps in a backlog's Open and Later sections that carry `due: YYYY-MM-DD`,
for the fleet watcher's `due` lines.
"""
import datetime as dt
import re

import fleet

LIST_ENTRY = re.compile(r"^(\s*)(?:[-*+]|(\d+)[.)])\s+")
CODE_FENCE = re.compile(r"^\s*(?:```(?!.*`)|~~~)")
HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
CHECKED_BOX = re.compile(r"^\[[xX]\]")
PARENT_CHARS = 40
STRUCK_THROUGH = re.compile(r"~~.*?~~")
DUE = re.compile(r"\bdue: ?`?(\d{4}-\d{2}-\d{2})(?!\d)", re.IGNORECASE)
DUE_TEXT_CHARS = 140
DUE_KEY_CHARS = 60


def dated_steps(open_text):
    """(date, text, shown) for each list entry in a backlog's Open section that carries due: YYYY-MM-DD.

    An entry starts with -, *, + or a number followed by . or ). A line continues it when it is
    indented or follows the entry's last line directly; a number other than 1 there continues it too,
    as in Markdown, when it is indented past the entry's marker. A nested entry is shown after its parent's
    first words. Code fences, ~~struck-through~~ text and entries checked off with [x], or under one, are
    skipped."""
    entries, entry, in_fence, after_blank, section_start = [], None, False, False, 0
    for line in open_text.splitlines():
        marker = LIST_ENTRY.match(line)
        body = line[marker.end():] if marker else line
        if CODE_FENCE.match(body):
            in_fence = not in_fence
            if marker:
                entry = {"indent": len(marker.group(1)), "lines": [], "parent": None}
                entries.append(entry)
            continue
        if in_fence:
            continue
        if not line.strip():
            after_blank = True
            continue
        wrapped_number = (marker and marker.group(2) not in (None, "1") and entry is not None
                          and not after_blank and len(marker.group(1)) > entry["indent"])
        if marker and not wrapped_number:
            indent = len(marker.group(1))
            parent = next((e for e in reversed(entries[section_start:]) if e["indent"] < indent), None)
            entry = {"indent": indent, "lines": [body.strip()], "parent": parent}
            entries.append(entry)
        elif entry is not None and not HEADING.match(line) and (line[0].isspace() or not after_blank):
            entry["lines"].append(line.strip())
        else:
            entry = None
            if HEADING.match(line):
                section_start = len(entries)
        after_blank = False

    def plain(e):
        return STRUCK_THROUGH.sub("", " ".join(e["lines"])).replace("**", "").strip()

    def checked_off(e):
        return e is not None and (bool(CHECKED_BOX.match(plain(e))) or checked_off(e["parent"]))

    steps = []
    for e in entries:
        text = plain(e)
        if checked_off(e):
            continue
        dates = []
        for date in DUE.findall(text):
            try:
                dt.date.fromisoformat(date)
            except ValueError:
                continue
            dates.append(date)
        shown = text
        if dates and e["parent"] and plain(e["parent"]):
            parent = plain(e["parent"])
            shown = f"{parent if len(parent) <= PARENT_CHARS else parent[:PARENT_CHARS - 1] + '…'} › {text}"
        steps += [(date, text, shown) for date in dict.fromkeys(dates)]
    return steps


def due_lines(domain, today, fired):
    """A `due` line for each dated step in the domain's Open or Later section whose date has come and that has had
    no line, and the key of every dated step whose date has come. A step's key is its date and the first
    DUE_KEY_CHARS characters of its own text, so an edit further on does not print it again. Steps with the
    same key are numbered in order. The lines are None when the backlog cannot be read."""
    try:
        open_text = fleet.open_and_later(fleet.domain_files(domain))
    except Exception:
        return None, fired
    lines, come = [], []
    for date, text, shown in dated_steps(open_text):
        if date > today:
            continue
        key = f"{date} {text[:DUE_KEY_CHARS]}"
        repeats = sum(k == key or k.startswith(f"{key}\n") for k in come)
        key = f"{key}\n{repeats + 1}" if repeats else key
        if key in come:
            continue
        come.append(key)
        if key not in fired:
            lines.append(f"due {date} · {shown if len(shown) <= DUE_TEXT_CHARS else shown[:DUE_TEXT_CHARS - 1] + '…'}")
    return lines, come

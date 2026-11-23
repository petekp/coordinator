#!/usr/bin/env python3
"""
Check a domain's notes: their size, and what a trim must not lose.

  backlog_check.py <domain>
  backlog_check.py <domain> --before <file> [--before <later file>] [--ledger <file>]
  backlog_check.py <domain> --since <commit> [--ledger <file>]
  backlog_check.py <domain> --cut-done

The notes are the domain's notes file, <domain>-notes.md or the older
<domain>-backlog.md, and the second file <domain>-later.md beside it when
it exists. backlog.py says how the notes file's shape sets the limits.

The first form prints the size of each section against the limits in the
config. The second compares the notes with copies saved before a trim:
the notes file, and the second file when there was one. The third does the
same with both files' text at <commit>, when Git tracks them. A file
missing at <commit> counts as empty. A LOST line is something the trim must
put back:

  - an entry of Standing yes, unless a ledger entry repeats it and quotes
    the user's dated words that end it, as in
    `- Merge on green CI (03-02: "Yes"). Ended: (04-01: "Ask me again")`
  - the session id of a live delegate of this domain, which the cap check
    and the status report find in the Open section
  - an open pull request from your GitHub account that the old text named
  - the user's words that the old text quoted outside Done and the new
    text quotes nowhere. The check finds a quote after a date, as in
    `(03-14: "Drop it")`, a quoted answer after a quoted question, and a
    quote after a name in the config's user_names
  - a sentence outside Done that held an open ask and is gone: one that
    starts `Ask:`, says "open before restart", or matches a pattern in
    the config's open_ask_patterns

The ledger is the trim's record of what it dropped on purpose and why. A
quote is not LOST for each time the ledger quotes exactly those words, and
a sentence for each time the ledger repeats it. The ledger clears no other
kind of LOST line.

A CHECK line is something to put back unless it is finished or answered:

  - the session id of a delegate this domain ended with delegate_end.py
  - a repo the backlog no longer links or names

Every form also prints a CHECK line for each local path outside Done that
no longer exists, and for a second file that the scripts do not read
because the notes file still has its own Done section. A local path starts
with ~/ or with a folder this machine has at its root, other than /dev.
One followed by more words on its line counts as there when adding some of
them names a file, since a name can hold spaces.

--cut-done deletes the oldest lines of Done until Done is at most half its
limit. When Git tracks the file that holds Done, it deletes only lines the
last commit holds, so Git keeps each one. Otherwise it first saves them to
<notes_dir>/trims/.

Exit status: 0 within the limits with no LOST line, 1 over a limit or a
LOST line, 2 the check could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog  # noqa: E402
import fleet  # noqa: E402
import notes_files  # noqa: E402
from done_cut import done_cut_to_half  # noqa: E402
from fleet import CONFIG, CheckError  # noqa: E402

QUOTED = r"[\"“]([^\"“”]{2,})[\"”]"
QUOTE_AFTER_A_DATE = re.compile(r"(?<![\d-])(?:\d{4}-)?\d{1,2}-\d{1,2}(?![\d-])[^\"“”\n.]{0,20}?[:,]\s*" + QUOTED)
ANSWER_AFTER_A_QUESTION = re.compile(r"\?[\"”][:,]\s*" + QUOTED)
WORDS_BETWEEN_A_NAME_AND_ITS_QUOTE = r"(?:[^\"“”\n.]|(?<=\d)\.(?=\d)){0,40}?[:,]?\s*"
ANY_QUOTED_TEXT = re.compile(QUOTED)
STARTS_AN_ASK = re.compile(r"^(?:[-*+]|\d+[.)])?\s*(?:\*\*)?Ask:")
OPEN_BEFORE_RESTART = re.compile(r"\bopen before restart\b", re.IGNORECASE)
END_OF_SENTENCE = re.compile(r"(?<=[.?!])[\"”)*]*\s+(?=[-*`(\"“A-Z0-9])")
TRIMS_DIR = fleet.BACKLOG_DIR / "trims"


def configured_patterns(key: str, wrap) -> list[re.Pattern]:
    try:
        return [re.compile(wrap(pattern), re.IGNORECASE) for pattern in CONFIG[key]]
    except (re.error, TypeError) as error:
        raise CheckError(f"{key} in {fleet.CONFIG_FILE} holds a pattern that is not a regular expression: {error}")


def quote_patterns() -> list[re.Pattern]:
    names = configured_patterns("user_names", lambda name: rf"(?<!\w)(?:{name})(?!\w)" + WORDS_BETWEEN_A_NAME_AND_ITS_QUOTE + QUOTED)
    return [QUOTE_AFTER_A_DATE, ANSWER_AFTER_A_QUESTION, *names]


def collapsed(text: str) -> str:
    return " ".join(text.split())


def quotes_of_the_user(text: str, patterns: list[re.Pattern]) -> list[str]:
    found = {(match.start(1), collapsed(match.group(1))) for pattern in patterns for match in pattern.finditer(text)}
    return [quote for _, quote in sorted(found)]


def sentences(text: str) -> list[str]:
    """Each sentence of each list item or paragraph, so that one changed sentence in a long item reads as one loss."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("- ", "* ", "#")):
            blocks.append([])
        blocks[-1].append(line)
    return [s for block in blocks for s in END_OF_SENTENCE.split(collapsed(" ".join(block))) if s]


def holds_an_open_ask(sentence: str, extra: list[re.Pattern]) -> bool:
    return bool(STARTS_AN_ASK.match(sentence) or OPEN_BEFORE_RESTART.search(sentence)
                or any(pattern.search(sentence) for pattern in extra))


def lines_the_last_commit_holds(path: Path) -> set[str] | None:
    """The lines of the file at HEAD, an empty set when Git tracks it but HEAD has none of it, or None when
    Git does not track it."""
    if fleet.git(str(path.parent), "ls-files", "--error-unmatch", path.name) is None:
        return None
    try:
        return set(text_at_commit(path, "HEAD").splitlines())
    except CheckError:
        return set()


def text_at_commit(path: Path, commit: str, also_as: tuple[Path, ...] = ()) -> str:
    """The file's text at the commit, or the text of the first of also_as that the commit holds."""
    top = fleet.git(str(path.parent), "rev-parse", "--show-toplevel")
    if top is None:
        raise CheckError(f"{path} is in no Git repo, so --since cannot read its earlier text. "
                         "Pass --before <file> with a copy saved before the trim")
    relatives = [os.path.relpath(os.path.realpath(path), os.path.realpath(top))]
    relatives += [os.path.relpath(os.path.join(os.path.realpath(other.parent), other.name), os.path.realpath(top))
                  for other in also_as]
    for relative in relatives:
        text = fleet.git(top, "show", f"{commit}:{relative}")
        if text is not None:
            return text
    raise CheckError(f"no {relatives[0]} at commit {commit} in {top}")


def texts_at_commit(files: notes_files.DomainFiles, commit: str) -> str:
    """Both files at the commit as one text. A second file the commit does not hold counts as empty."""
    other_name = files.notes.parent / (files.domain + (notes_files.OLDER_NOTES_SUFFIX
                                                      if files.notes.name.endswith(notes_files.NOTES_SUFFIX)
                                                      else notes_files.NOTES_SUFFIX))
    notes_text = text_at_commit(files.notes, commit, also_as=(other_name,))
    try:
        later_text = text_at_commit(files.later, commit)
    except CheckError:
        later_text = ""
    return notes_files.joined(notes_text, later_text)


def entries(section: str) -> list[str]:
    """Each top-level list item or paragraph of a section, with its whitespace collapsed."""
    found: list[list[str]] = []
    for line in section.splitlines():
        starts_one = line.startswith("- ") or not line.strip()
        if starts_one or not found:
            found.append([])
        found[-1].append(line)
    return [text for text in (collapsed(" ".join(lines)) for lines in found) if text]


def ended_in_the_users_words(entry: str, ledger_entries: list[str], patterns: list[re.Pattern]) -> bool:
    """A ledger entry repeats the Standing yes entry and, outside that copy, quotes the user's dated words."""
    return any(entry in listed and quotes_of_the_user(listed.replace(entry, " ", 1), patterns)
               for listed in ledger_entries)


def compared_with(herd: fleet.Fleet, domain: str, before: str, after: str, ledger: str = "") -> tuple[list[str], list[str]]:
    """What the trim lost, and what to check that it meant to drop."""
    lost, check = [], []
    listed = collapsed(ledger)
    quoted_in_ledger = [collapsed(quote) for line in ledger.splitlines() for quote in ANY_QUOTED_TEXT.findall(line)]
    patterns = quote_patterns()
    ledger_entries = entries(ledger)
    standing_yes_after = collapsed(fleet.section_text(after, "## Standing yes"))
    for entry in entries(fleet.section_text(before, "## Standing yes")):
        if entry not in standing_yes_after and not ended_in_the_users_words(entry, ledger_entries, patterns):
            lost.append(f"Standing yes entry: {entry[:120]}")

    outside_done_before = before.replace(notes_files.done_text(before), "")
    open_after = fleet.section_text(after, "## Open")

    def dropped_from_open(session: str) -> bool:
        short = session[:fleet.SHORT_SESSION_ID]
        return len(short) == fleet.SHORT_SESSION_ID and short in outside_done_before and short not in open_after

    for member in herd.members_of(domain):
        session = fleet.session_of(member["agent"])
        if dropped_from_open(session):
            lost.append(f"session of the live delegate {member['agent']['name']}, missing from Open: {session}")
    for record in fleet.ended_records(domain):
        if dropped_from_open(record["session"]):
            check.append(f"session of the ended delegate {record['name']}, missing from Open: {record['session']}. "
                         "Keep its handle unless its task finished")

    # A copy that survives elsewhere, or one ledger line, must not excuse every dropped copy of the same words.
    quoted_before = quotes_of_the_user(outside_done_before, patterns)
    quoted_after = quotes_of_the_user(after, patterns)
    for quote in dict.fromkeys(quoted_before):
        gone = quoted_before.count(quote) - quoted_after.count(quote) - quoted_in_ledger.count(quote)
        if gone > 0:
            lost.append(f"the user's words, {gone} of {quoted_before.count(quote)} places: \"{quote}\"")
    extra = configured_patterns("open_ask_patterns", lambda pattern: pattern)
    asks_before = [s for s in sentences(outside_done_before) if holds_an_open_ask(s, extra)]
    kept = collapsed(after)
    for sentence in dict.fromkeys(asks_before):
        gone = asks_before.count(sentence) - kept.count(sentence) - listed.count(sentence)
        if gone > 0:
            lost.append(f"sentence that held an open ask: {sentence}")

    repos = sorted(fleet.backlog_repos(before, herd.checkouts))
    open_numbers = {slug: fleet.open_pull_request_numbers(slug) for slug in repos}
    for slug, number in sorted(backlog.pull_requests_named(before, open_numbers) - backlog.pull_requests_named(after, open_numbers)):
        lost.append(f"open pull request: {slug}#{number}")
    for slug in sorted(set(repos) - fleet.backlog_repos(after, herd.checkouts)):
        check.append(f"repo the backlog no longer links or names: {slug}. When the domain owns it, name its checkout "
                     "by its path in Boundary, or the fleet scripts stop counting its pull requests")
    return lost, check


def cut_done(notes: backlog.Notes) -> backlog.Notes:
    """Cut the Done section of the file that holds it, and the notes read again after the cut."""
    files = notes.files
    path, text = (files.later, notes.later_text) if notes.two_files else (files.notes, notes.text)
    held = lines_the_last_commit_holds(path)
    after, deleted, kept_uncommitted, done_size = done_cut_to_half(text, dt.date.today(), notes.done_limit, held)
    if deleted:
        kept_by = "Git keeps them at HEAD"
        if held is None:
            TRIMS_DIR.mkdir(parents=True, exist_ok=True)
            saved = TRIMS_DIR / f"{files.domain}-done-cut-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            saved.write_text("\n".join(deleted) + "\n")
            kept_by = f"saved them to {saved}"
        if path.read_text() != text:
            raise CheckError(f"{path} changed while the cut ran, so nothing was cut. Run --cut-done again")
        path.write_text(after)
        print(f"deleted the {sum(line.startswith('- ') for line in deleted)} oldest lines of Done; {kept_by}")
    else:
        print("deleted no line of Done")
    if kept_uncommitted and done_size > notes.done_limit // 2:
        print(f"kept {kept_uncommitted} items of Done that the last commit does not hold. Commit {path.name} "
              "and run --cut-done again to cut them")
    return backlog.Notes(files)


def sizes(notes: backlog.Notes) -> list[str]:
    def rows(text: str, where: str = "") -> list[str]:
        return [f"{backlog.size(body) / backlog.BYTES_PER_KB:8.1f} KB  {heading or '(before the first heading)'}{where}"
                for heading, body in backlog.sections(text).items() if heading or body.strip()]

    if not notes.two_files:
        return rows(notes.text)
    total = (f"{backlog.size(notes.text) / backlog.BYTES_PER_KB:8.1f} KB  {notes.files.notes.name} in all, "
             f"limit {notes.read_at_start_limit // backlog.BYTES_PER_KB}")
    return rows(notes.text) + [total] + rows(notes.later_text, f", in {notes.files.later.name}")


def checks_of_the_files(notes: backlog.Notes) -> list[str]:
    files = notes.files
    found = []
    if files.stray:
        found.append(notes.stray_warning())
    if notes.second_file_ignored:
        found.append(f"{files.later.name} exists, but {files.notes.name} still has a ## Done section, so its limits "
                     f"are those of a file in one piece and --resume prints nothing of {files.later.name}. Move Later "
                     f"and Done into {files.later.name}, or move its items into {files.notes.name}")
    if notes.two_files and "## Later" in backlog.sections(notes.text):
        found.append(f"{files.notes.name} has a ## Later section, which belongs in {files.later.name}")
    found += [f"local path that does not exist: {written}, in {where}" for written, where in notes.dead_links()]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain")
    earlier = parser.add_mutually_exclusive_group()
    earlier.add_argument("--before", metavar="FILE", action="append",
                         help="a copy of the notes file saved before the trim; given again, of the second file")
    earlier.add_argument("--since", metavar="COMMIT", help="the commit made just before the trim")
    parser.add_argument("--ledger", metavar="FILE", help="the trim's record of what it dropped on purpose")
    parser.add_argument("--cut-done", action="store_true", help="delete the oldest lines of Done until it is at most half its limit")
    parser.add_argument("--terminals", metavar="FILE", help="read a saved JSON list of `hc get` output instead of running hc")
    args = parser.parse_args()
    if args.ledger and not (args.before or args.since):
        parser.error("--ledger needs --before <file> or --since <commit>")
    if args.before and len(args.before) > 2:
        parser.error("--before takes the notes file's copy and at most one more, the second file's")
    try:
        files = fleet.domain_files(args.domain)
        if not files.notes.is_file():
            raise CheckError(f"no backlog at {files.notes}")
        notes = backlog.Notes(files)
        if args.cut_done:
            notes = cut_done(notes)
        for row in sizes(notes):
            print(row)
        over = backlog.parts_over_their_limit(notes)
        for part in over:
            print(f"OVER  {part}")
        for item in checks_of_the_files(notes):
            print(f"CHECK  {item}")
        lost = []
        if args.before or args.since:
            if args.before:
                copies = [Path(copy).expanduser().read_text() for copy in args.before]
                before = notes_files.joined(copies[0], copies[1] if len(copies) > 1 else "")
            else:
                before = texts_at_commit(files, args.since)
            ledger = Path(args.ledger).expanduser().read_text() if args.ledger else ""
            lost, check = compared_with(fleet.Fleet(args.terminals), args.domain, before, notes.whole, ledger)
            for item in lost:
                print(f"LOST  {item}")
            for item in check:
                print(f"CHECK  {item}")
            print(f"compared with {' and '.join(args.before) if args.before else args.since}: "
                  f"{len(lost)} lost, {len(check)} to check")
    except (CheckError, OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"backlog_check: could not run: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 1 if over or lost else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The standing rules the watcher prints after its events: notes over a limit, quiet delegates and Later items to review."""
from __future__ import annotations

import time

import backlog
import fleet

LATER_REVIEW_EVERY_SECONDS = 7 * 24 * 3600


def later_review_lines(notes, domain, reviewed_at, now):
    """One line asking for a review of the Later items that --resume hides, for a notes file in two files, at most
    once every LATER_REVIEW_EVERY_SECONDS. It records the time in reviewed_at, which maps each domain to a time."""
    hidden, shown = notes.later_hidden_and_shown() if notes.two_files else ([], [])
    last = reviewed_at.get(domain)
    printed_lately = isinstance(last, (int, float)) and 0 <= now - last < LATER_REVIEW_EVERY_SECONDS
    if not hidden or printed_lately:
        return []
    reviewed_at[domain] = now
    return [f"in your notes, --resume hides the oldest {len(hidden)} of {len(hidden) + len(shown)} Later items · "
            f"Bring the user those {len(hidden)}, which start on lines {hidden[0].line} to {hidden[-1].line} of "
            f"{notes.files.later}, as one question: keep or drop each."]


def rule_lines(domain, later_reviewed=None):
    """Standing rules for the domain's coordinator, each with its command."""
    herd = fleet.Fleet()
    if domain not in herd.backlogs:
        return []
    notes = backlog.Notes(herd.files(domain))
    reviewed_at = {} if later_reviewed is None else later_reviewed
    rules = notes.standing_rules() + later_review_lines(notes, domain, reviewed_at, time.time())
    lines = [f"rule · {rule}" for rule in rules]
    now = fleet.dt.datetime.now(fleet.dt.timezone.utc)
    quiet = 0
    for member in herd.members_of(domain) if domain not in fleet.DOMAINS_KEPT_RUNNING else []:
        agent = member["agent"]
        if agent["status"] in ("working", "blocked"):
            continue
        responded = fleet.last_response(fleet.session_of(agent), now)
        if responded and now - responded >= fleet.IDLE_BEFORE_ENDING:
            quiet += 1
    if quiet:
        lines.append(f"rule · {quiet} of your delegates quiet for over a day · list them with "
                     f"{fleet.SCRIPTS}/delegate_end.py --list-idle and end each one")
    return lines

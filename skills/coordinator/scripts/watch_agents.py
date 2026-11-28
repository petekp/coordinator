"""
How the fleet watcher reads its delegates: a snapshot of each one on every
poll, the events between two snapshots, and the counts that a delegate's
REPORT.md and saved inbox add to its settled and report lines.
"""
import os
import re

import fleet
import inbox_state
from watch_domain import named

UNKNOWN_SECONDS = 300
SETTLED = {"idle", "done", "blocked", "error"}
REPORT_HEADINGS = (("questions for the user", "question", "questions"), ("issues filed", "issue", "issues"),
                   ("findings", "finding", "findings"))
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.*?)[\s#]*$")
TOP_LEVEL_LIST_ENTRY = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)")
NO_ENTRY_WORDS = {"none", "nothing", "n/a"}


def ended_delegates():
    """Records of delegates ended with delegate_end.py."""
    try:
        return fleet.ended_records()
    except Exception:
        return []


def was_ended(name, was, ended):
    """By session when the watcher saw the delegate alive, so a reused name is not mistaken for it."""
    records = [record for record in ended if isinstance(record, dict)]
    if was.get("session"):
        return any(record.get("session") == was["session"] for record in records)
    return any(record.get("name") == name for record in records)


def transcript_marks(agent):
    try:
        return fleet.transcript_marks(fleet.session_of(agent))
    except Exception:
        return None, None


def report_mtime(cwd, name, cwd_is_shared):
    """A repo delegate reports in its worktree's .scratch; a delegate with no repo reports beside its brief.

    A .scratch/REPORT.md in a cwd that another watched delegate shares
    cannot be told apart, so it is not read.
    """
    candidates = [os.path.join(cwd, ".scratch", "REPORT.md")] if cwd and not cwd_is_shared else []
    candidates.append(os.path.join(fleet.BRIEFS_DIR, name, "REPORT.md"))
    for path in candidates:
        if os.path.exists(path):
            return path, os.path.getmtime(path)
    return None, None


def report_counts(path):
    """The list entries under each of the report's fixed headings, as in "2 questions, 0 issues", or None
    when it has none of them."""
    try:
        with open(path) as report:
            text = report.read()
    except (OSError, TypeError, UnicodeDecodeError):
        return None
    titles = {title for title, _, _ in REPORT_HEADINGS}
    counts, current, in_fence = {}, None, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        heading = None if in_fence else MARKDOWN_HEADING.match(line)
        if heading:
            title = heading.group(1).strip().rstrip(":").strip().lower()
            current = title if title in titles else None
            if current:
                counts.setdefault(current, 0)
            continue
        entry = current and not in_fence and TOP_LEVEL_LIST_ENTRY.match(line)
        first_word = entry and (entry.group(1).split() or [""])[0].rstrip(".,:;!").lower()
        if entry and first_word not in NO_ENTRY_WORDS:
            counts[current] += 1
    if not counts:
        return None
    return ", ".join(f"{counts[title]} {one if counts[title] == 1 else many}"
                     for title, one, many in REPORT_HEADINGS if title in counts)


def findings_note(count):
    return f" · {count} finding{'s' if count != 1 else ''} in its inbox" if count else ""


def saved_inbox_reader():
    """Reads the inbox mod's saved state once, on first use. None when it cannot be read."""
    loaded = []

    def read():
        if not loaded:
            try:
                loaded.append(inbox_state.saved_inbox(inbox_state.inbox_store()))
            except Exception:
                loaded.append(None)
        return loaded[0]
    return read


def agent_snapshot(names, agents, seen, clock, ended=(), saved_inbox=None):
    saved_inbox = saved_inbox or saved_inbox_reader()
    by_name = named(agents)
    watched_cwds = [by_name[name].get("cwd") for name in names if name in by_name]
    snap = {}
    for name in names:
        a = by_name.get(name)
        was = seen.get(name, {})
        if a is None:
            snap[name] = {"present": False, "ended": was_ended(name, was, ended), "session": was.get("session")}
            continue
        status = a["status"]
        turn_ended = status in SETTLED and (a["turn"] != was.get("turns") or was.get("status") not in SETTLED)
        still_running = a.get("still_running") or ""
        if turn_ended or "responded" not in was:
            responded, compacted = transcript_marks(a)
        else:
            responded, compacted = was.get("responded"), was.get("compacted")
        cwd = a["cwd"]
        path, mtime = report_mtime(cwd, name, watched_cwds.count(cwd) > 1)
        if status is not None and status not in SETTLED and not still_running:
            mtime = was.get("report_mtime", mtime)
        report_written_with_tasks_running = bool(still_running) and mtime != was.get("report_mtime")
        if turn_ended or "findings" not in was or report_written_with_tasks_running:
            inbox = saved_inbox()
            findings = len(inbox_state.open_findings(inbox.get(a["session"]))) if inbox is not None else was.get("findings")
        else:
            findings = was.get("findings")
        counts = was.get("report_counts") if mtime == was.get("report_mtime") and "report_counts" in was \
            else report_counts(path)
        unknown_since = (was.get("unknown_since") or clock.read()) if status is None else None
        without_status_too_long = unknown_since is not None and (
            bool(was.get("stuck")) or clock.awake_seconds_since(unknown_since) >= UNKNOWN_SECONDS)
        snap[name] = {
            "present": True,
            "status": status,
            "turns": a["turn"],
            "asks": a["inbox_asks"],
            "inbox": f"{a['inbox_asks']} · {a['inbox_status']}",
            "report": path,
            "report_mtime": mtime,
            "report_counts": counts,
            "findings": findings,
            "unknown_since": unknown_since,
            "stuck": without_status_too_long,
            "responded": responded,
            "compacted": compacted,
            "session": a["session"],
            "still_running": still_running,
        }
    return snap


def only_compacted(was, now):
    """The transcript gained a compaction and no assistant entry since the last snapshot."""
    return bool(
        now.get("compacted") and now["compacted"] != was.get("compacted")
        and was.get("responded") and now.get("responded") == was["responded"]
    )


def agent_events(old, new):
    for name, now in new.items():
        was = old.get(name)
        if was is None:
            continue
        if was.get("present") and not now["present"]:
            if not now.get("ended"):
                yield f"gone {name}"
            continue
        if not now["present"] or not was.get("present"):
            continue
        became_settled = now["status"] in SETTLED and was.get("status") not in SETTLED
        turn_ended = now["status"] in SETTLED and (now["turns"] != was.get("turns") or became_settled)
        if turn_ended and (now["status"] in ("blocked", "error") or not only_compacted(was, now)):
            yield f"settled {name} {now['status']}{findings_note(now.get('findings'))}"
        if now["stuck"] and not was.get("stuck"):
            yield f"unknown {name}"
        if now["asks"] > was.get("asks", 0):
            yield f"ask {name} {now['inbox']}"
        report_moved = bool(was.get("report")) and now["report"] != was.get("report")
        if now["report_mtime"] and now["report_mtime"] != was.get("report_mtime") and not report_moved:
            counts = f" · {now['report_counts']}" if now.get("report_counts") else ""
            running = f" · {now['still_running']}" if now.get("still_running") else ""
            yield f"report {name} {now['report']}{counts}{findings_note(now.get('findings'))}{running}"

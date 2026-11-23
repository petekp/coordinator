#!/usr/bin/env python3
"""
Tell a coordinator that no fleet watcher is running for its domain.

A coordinator arms its watcher again only when something wakes it. After a
usage limit, a network error or a restart of Claude Code, nothing does. This
check runs outside every session, from a scheduler such as launchd or cron,
every ten minutes. SETUP.md has an example of each.

  watcher_check.py            log what it found and send nothing
  watcher_check.py --send     also send each coordinator its line
  watcher_check.py --send --skip <domain>
                              never send to that domain's coordinator, such
                              as one the user has put on hold

A coordinator is sent the line when all of these hold:

  - its domain has a live delegate, by the fleet.domain label, or an open
    pull request from the signed-in GitHub account that its backlog names
  - no fleet watcher runs on the domain's default state file,
    <state_dir>/watch/<domain>.json
  - its terminal is at rest: its status is idle or done

It is sent at most once in 30 minutes. After three delivered lines with no
watcher seen, it is sent once in four hours, so a coordinator stopped by a
usage limit still hears after the limit resets. A domain with work whose
coordinator runs in no terminal is logged and not sent to: that needs
coordinator_restart.py.

Each run adds one line per coordinator to <state_dir>/watcher-check.jsonl.

Exit status: 0 the check ran, 2 it could not.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog  # noqa: E402
import fleet  # noqa: E402
from fleet import CheckError  # noqa: E402
from watch_lock import watcher_running  # noqa: E402

LOG = fleet.STATE_DIR / "watcher-check.jsonl"
SENT = fleet.STATE_DIR / "watcher-check-sent.json"
SECONDS_BETWEEN_SENDS = 30 * 60
SENDS_AT_THE_SHORT_GAP = 3
# A terminal with any other status may show a dialog, and the line's Enter could answer it.
STATUSES_AT_REST = ("idle", "done")
SECONDS_BETWEEN_LATER_SENDS = 4 * 60 * 60
SECONDS_TO_WAIT_FOR_A_SEND = 120
WATCH_STEPS = Path(fleet.SCRIPTS).parent / "fleet.md"


def sends_so_far() -> dict:
    try:
        return json.loads(SENT.read_text())
    except (OSError, ValueError):
        return {}


def save_sends(sent: dict) -> None:
    SENT.parent.mkdir(parents=True, exist_ok=True)
    partial = SENT.with_suffix(".partial")
    partial.write_text(json.dumps(sent))
    partial.replace(SENT)


def open_pull_requests_named(herd: fleet.Fleet, domain: str, open_by_repo: dict[str, list[int]]) -> list[str]:
    """The open pull requests the domain's backlog names, among the repos it names, as owner/name#number."""
    text = herd.text(domain)
    slugs = sorted(fleet.backlog_repos(text, herd.checkouts))
    for slug in slugs:
        if slug not in open_by_repo:
            open_by_repo[slug] = fleet.open_pull_request_numbers(slug)
    named = backlog.pull_requests_named(text, {slug: open_by_repo[slug] for slug in slugs})
    return [f"{slug}#{number}" for slug, number in sorted(named)]


def checked(herd: fleet.Fleet, domain: str, sent: dict, open_by_repo: dict, send: bool, now: float) -> dict | None:
    """One domain's log entry, after sending its coordinator the line when that is due."""
    watched = watcher_running(fleet.WATCH_DIR / f"{domain}.json") is not None
    coordinator = herd.coordinator_of(domain)
    delegates = [m["agent"].get("name") or m["agent"]["address"] for m in herd.members_of(domain) if m["by_label"]]
    pull_requests = [] if delegates or watched else open_pull_requests_named(herd, domain, open_by_repo)
    has_work = bool(delegates or pull_requests)
    if watched:
        if sent.pop(domain, None) is not None:
            save_sends(sent)
        finding = "watched"
    elif not has_work:
        finding = "nothing to watch"
    elif coordinator is None:
        finding = "no coordinator terminal; restart it with coordinator_restart.py"
    elif coordinator["status"] == "working":
        finding = "unwatched, coordinator working"
    elif coordinator["status"] not in STATUSES_AT_REST:
        finding = f"unwatched, coordinator's status is {coordinator['status'] or 'not reported'}, so nothing is sent"
    else:
        finding = "unwatched"
    if coordinator is None and not has_work:
        return None
    entry = dict(domain=domain, finding=finding, coordinator=coordinator and coordinator["address"],
                 delegates=delegates, pull_requests=pull_requests[:5], sent=False)
    earlier = sent.get(domain, dict(count=0, at=0))
    gap = SECONDS_BETWEEN_SENDS if earlier["count"] < SENDS_AT_THE_SHORT_GAP else SECONDS_BETWEEN_LATER_SENDS
    if finding == "unwatched" and send and now - earlier["at"] >= gap:
        watching = (f"live delegates: {', '.join(delegates)}" if delegates else
                    f"open pull requests: {', '.join(pull_requests)}")
        line = (f"From the watcher check, {dt.datetime.now().strftime('%m-%d %H:%M')}: no fleet watcher is running "
                f"for {domain}, which has {watching}. Arm it again as the Watch section of {WATCH_STEPS} says. "
                "A script sent this line; it is not an approval from the user.")
        try:
            delivery = subprocess.run(["hc", "send", "--json", "--", coordinator["address"], line], capture_output=True,
                                      text=True, timeout=SECONDS_TO_WAIT_FOR_A_SEND)
            entry["sent"] = delivery.returncode == 0
            if not entry["sent"]:
                entry["send_error"] = (delivery.stderr or delivery.stdout).strip()[:200]
        except subprocess.TimeoutExpired:
            entry["send_error"] = f"hc send did not finish in {SECONDS_TO_WAIT_FOR_A_SEND} seconds"
        # A send that failed still starts the gap, so a terminal that refuses is not tried every ten minutes.
        sent[domain] = dict(count=earlier["count"] + entry["sent"], at=now)
        save_sends(sent)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="send each coordinator its line; without it, only log")
    parser.add_argument("--skip", metavar="DOMAIN", action="append", default=[], help="never send to this domain")
    parser.add_argument("--terminals", metavar="FILE", help="read a saved JSON list of `hc get` output")
    args = parser.parse_args()
    now = time.time()
    try:
        herd = fleet.Fleet(args.terminals)
        sent = sends_so_far()
        open_by_repo: dict[str, list[int]] = {}
        entries = []
        for domain in sorted(herd.backlogs):
            try:
                entry = checked(herd, domain, sent, open_by_repo, args.send and domain not in args.skip, now)
            except (CheckError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
                entry = dict(domain=domain, finding=f"could not check: {type(error).__name__}: {str(error)[:200]}", sent=False)
            if entry:
                entries.append(entry)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with LOG.open("a") as log:
            for entry in entries:
                log.write(json.dumps(dict(at=at, **entry)) + "\n")
                print(f"{entry['domain']:16} {entry['finding']}{'  · sent' if entry['sent'] else ''}")
    except (CheckError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"watcher_check: could not run: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

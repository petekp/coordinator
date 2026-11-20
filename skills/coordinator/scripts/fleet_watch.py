#!/usr/bin/env python3
"""Wait until something in a coordinator's fleet needs attention, then exit.

  fleet_watch.py --domain DOMAIN [--agent NAME]... [--pr REPO#N]...
                 [--state FILE] [--owner TERMINAL]
  fleet_watch.py [--agent NAME]... [--pr REPO#N]... [--state FILE]
                 [--owner TERMINAL]

The state file defaults to <state_dir>/watch/<domain>.json, for --domain
or else the domain of the coordinator in this terminal.

Watches delegates and pull requests, prints one line per event, and exits.
With --domain it finds them on each poll: the domain's live delegates, as
fleet.py finds them, and the open pull requests from the signed-in GitHub
account that the domain's backlog names, as fleet_status.py finds them. A
delegate or PR that joins while it runs is watched from then on, with no
line for joining. One that leaves the domain while it still runs, or a PR
that stays open after the backlog stops naming it, is dropped with no line.
A PR whose repo the domain no longer lists stays watched while the backlog
still names it, until it leaves the open state.
--agent and --pr name more to watch, or, without --domain, all of them.
An agent is named by its herdr-core terminal name or address, a PR by its
checkout's path or owner/name, and its number. The owner is
the terminal the watcher reports to: by default the one it runs in.
herdr-core is polled every 5 seconds and GitHub every 90. An urgent
event exits after a 15-second pause that gathers events that happen
together. Other events wait until 10 minutes after the last exit.

Urgent events:

  ask <agent> <count> · <text>   its inbox holds more items for the user
  gone <agent>                   no live terminal runs it any more
  unknown <agent>                its terminal has reported no status for 5 minutes
                                 with the Mac awake
  settled <agent> blocked        stopped on a dialog or a question
  settled <agent> error          stopped on an error: read its terminal
  pr <repo>#<n> merged|closed    the PR left the open state
  pr <repo>#<n> checks failure   checks finished and failed
  pr <repo>#<n> review changes_requested
  pr <repo>#<n> not found        GitHub has no such PR: fix the --pr argument

Other events:

  settled <agent> idle|done      finished a turn. A compaction while idle is
                                 not a turn and prints nothing.
  report <agent> <path>          its REPORT.md changed and it has settled
  pr <repo>#<n> checks success   checks finished and passed
  pr <repo>#<n> review <state>   review decision changed
  pr <repo>#<n> comments <n>     new reviews or comments from other accounts
  due <date> · <text>            with --domain: a list entry in the backlog's
                                 Open section carries due: <date>, and that
                                 date has come. An entry starts with -, *, +
                                 or a number and . or ). One checked off with
                                 [x], or under one, is skipped, and a nested
                                 one is shown after its parent's first words.
                                 It prints once for the date and the entry's
                                 own first 60 characters.

Lines that end the watch without an event:

  blind hc|backlog|<repo> · <reason>
                                 that source failed for 15 minutes with the Mac
                                 awake: arm again
  owner gone                     the owner's terminal ran no agent in two polls
  parent gone                    the shell that started it exited: arm again
  stopped                        you or a newer watcher stopped this one

Lines that end the watch with exit status 2:

  no agent <name>                no live agent has that name: fix or drop the --agent argument
  no backlog <domain> · <why>    --domain names no readable backlog with an Open section
  old state <file> · <what to do>
                                 --domain alone on a state file from a watcher
                                 older than --domain: arm once with --domain
                                 and the last watcher's --agent and --pr
  locked <pid>                   the watcher holding the state file's lock did not stop

Lines printed after the events, when the owner is a coordinator.
They never end the watch on their own:

  ended <agent> holds <repo>#<n> · resume: <command>
                                 an event above is for the PR of a delegate
                                 you ended with delegate_end.py. Run the
                                 command to give it the work.
  rule · <text>                  a standing rule and its command: the limit
                                 on open PRs once reached, delegates
                                 quiet for over a day, a backlog over its
                                 size limit, and an update to these skills,
                                 checked at most daily. Follow each.

A delegate ended with delegate_end.py prints no `gone` and no `no agent`.

`alive` is printed every 10 minutes while nothing fires, so Claude Code
sees output and sends no quiet-task check-in. It needs nothing.

The state file keeps the last snapshot, held events and the last exit time,
so a change made while the watch was not running fires once it is armed
again. The first run with a new state file records a snapshot and waits,
apart from `due` lines, which print once whenever their date has come.
A delegate that comes back after `gone` starts a new snapshot. It also
keeps what the watcher watched, and --domain starts from that list, so a
delegate or PR from a watcher armed with --agent and --pr keeps its events.
A delegate or PR named one way by --agent or --pr and another by --domain
(terminal address or name, checkout path or owner/name) shares one
snapshot, so switching between the forms neither loses nor repeats events.

A watcher holds a lock on <state>.lock while it runs. Starting one stops
the watcher holding that lock. Under Claude Code, a watcher started with a
trailing & cannot wake the session when it exits, so it refuses to start
and messages the owner instead.

While it runs, it labels its terminal fleet.waiting.<task ID>, as
waiting_label.py says.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

AGENT_POLL_SECONDS = 5
PR_POLL_SECONDS = 90
GATHER_SECONDS = 15
NON_URGENT_GAP_SECONDS = 600
ALIVE_SECONDS = 600
BLIND_SECONDS = 900
UNKNOWN_SECONDS = 300
COMMAND_TIMEOUT_SECONDS = 60
OWNER_MISSES_BEFORE_GONE = 2
NAME_MISSES_BEFORE_UNKNOWN = 2
SETTLED = {"idle", "done", "blocked", "error"}
URGENT = re.compile(r"^(ask|gone|unknown|owner|pr \S+ (merged|closed|checks failure|review changes_requested|not found))|(blocked|error)$")
STATE_SOURCE = "hc"
RULE_SECONDS = 20

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import backlog  # noqa: E402
import fleet  # noqa: E402
from due_steps import due_lines  # noqa: E402
from mac_clock import MacClock  # noqa: E402
from update_check import update_line  # noqa: E402
from waiting_label import declare_waiting  # noqa: E402
from watch_domain import (adopt_earlier_pr, copy_to_same_pr, copy_to_same_terminal, domain_member_names,  # noqa: E402
                          domain_pull_requests, given_terminals, named, slug_spec, under_watch_names,
                          watched_agent_names, watched_pr_specs, written_before_domain)
from watch_lock import take_state_lock  # noqa: E402
from watch_prs import ended_lines, pr_events, pr_snapshot  # noqa: E402


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


def live_agents():
    """Every live agent, or None when herdr-core could not be read."""
    try:
        return fleet.load_agents()
    except Exception:
        return None


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


def agent_snapshot(names, agents, seen, clock, ended=()):
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
        if turn_ended or "responded" not in was:
            responded, compacted = transcript_marks(a)
        else:
            responded, compacted = was.get("responded"), was.get("compacted")
        cwd = a["cwd"]
        path, mtime = report_mtime(cwd, name, watched_cwds.count(cwd) > 1)
        if status not in SETTLED:
            mtime = was.get("report_mtime", mtime)
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
            "unknown_since": unknown_since,
            "stuck": without_status_too_long,
            "responded": responded,
            "compacted": compacted,
            "session": a["session"],
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
            yield f"settled {name} {now['status']}"
        if now["stuck"] and not was.get("stuck"):
            yield f"unknown {name}"
        if now["asks"] > was.get("asks", 0):
            yield f"ask {name} {now['inbox']}"
        report_moved = bool(was.get("report")) and now["report"] != was.get("report")
        if now["report_mtime"] and now["report_mtime"] != was.get("report_mtime") and not report_moved:
            yield f"report {name} {now['report']}"


class RulesTimeout(BaseException):
    pass


def rule_lines(domain):
    """Standing rules for the domain's coordinator, each with its command."""
    herd = fleet.Fleet()
    if domain not in herd.backlogs:
        return []
    lines = []
    for slug in sorted(herd.repos_of(domain) & set(fleet.PR_LIMITS)):
        limit = fleet.PR_LIMITS[slug]
        try:
            count = fleet.open_pull_request_count(slug)
        except Exception:
            lines.append(f"rule · could not count the open PRs in {slug}, limit {limit} · run {fleet.SCRIPTS}/delegate_cap.py "
                         "before starting work that opens one")
            continue
        if count >= limit:
            lines.append(f"rule · {slug} has {count} open PRs from your GitHub account, limit {limit} · start nothing that "
                         "opens a PR; work on an open PR goes ahead, and so does what the user asks for")
    text = herd.backlog(domain).read_text()
    for part in backlog.parts_over_their_limit(text):
        lines.append(f"rule · in your backlog, {part} · {backlog.what_to_do_when_over(text)}")
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


def print_rules(agents, owner, stop_waiting):
    """New lines may fail; the events above them are already printed and saved.

    A newer watcher's SIGTERM in this window ends the process without
    `stopped`, which would tell the coordinator to ignore the events above.
    """
    def give_up(signum, frame):
        raise RulesTimeout()
    signal.signal(signal.SIGTERM, lambda signum, frame: (stop_waiting(), os._exit(0)))
    try:
        owning_agent = next((a for a in agents or [] if a["address"] == owner), None)
        if not owning_agent or owning_agent["role"] != "coordinator" or not owning_agent["domain"]:
            return
        signal.signal(signal.SIGALRM, give_up)
        signal.alarm(RULE_SECONDS)
        for line in rule_lines(owning_agent["domain"]):
            print(line, flush=True)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        signal.alarm(0)
    print_update_line()


def print_update_line():
    try:
        signal.alarm(RULE_SECONDS)
        line = update_line(time.strftime("%Y-%m-%d"))
        if line:
            print(line, flush=True)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        signal.alarm(0)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def merged(old, new):
    """Keep entries that a failed poll left out of the new snapshot."""
    return {**old, **new}


def print_stopped_and_exit(signum, frame):
    print("stopped", flush=True)
    sys.exit(0)


def started_with_trailing_ampersand():
    """A trailing & leaves the process with SIGINT ignored, then orphaned once its shell exits."""
    detached = os.getppid() == 1 or signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    return os.environ.get("CLAUDECODE") == "1" and detached


def parent_shell_gone():
    """Claude Code quit or its Bash wrapper died. Claude Code reports a background task as ended
    when the wrapper dies, so the owner is not messaged: after a quit its terminal holds a plain shell."""
    return os.environ.get("CLAUDECODE") == "1" and os.getppid() == 1


def tell_owner_it_stopped(owner):
    message = ("fleet_watch.py stopped: it was started with a trailing & or lost its parent shell, "
               "so its exit cannot wake this session. Nothing is watching the fleet. "
               "Start it again with Bash run_in_background and no &.")
    print(message, file=sys.stderr, flush=True)
    if owner:
        try:
            subprocess.run(["hc", "send", owner, message], capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass


def owner_present(agents, owner, owner_session):
    """The owner's terminal still runs an agent, or its Claude session runs in another terminal."""
    return any(a["address"] == owner or (owner_session and a["session"] == owner_session) for a in agents)


def wake_time(first_event_at, first_urgent_at, last_wake_at):
    if first_urgent_at is not None:
        return first_urgent_at + GATHER_SECONDS
    if first_event_at is not None:
        return max(first_event_at + GATHER_SECONDS, last_wake_at + NON_URGENT_GAP_SECONDS)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", help="default: <state_dir>/watch/<domain>.json")
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--pr", action="append", default=[])
    parser.add_argument("--owner", default=os.environ.get("HC_TERMINAL_ID"))
    parser.add_argument("--domain")
    args = parser.parse_args()
    if not args.state:
        try:
            args.state = str(fleet.WATCH_DIR / f"{args.domain or fleet.Fleet().own_domain()}.json")
        except Exception as error:
            print(f"fleet_watch: pass --state, since the default needs this terminal's domain: {error}", flush=True)
            return 2
    args.state = os.path.realpath(os.path.expanduser(args.state))

    if started_with_trailing_ampersand():
        tell_owner_it_stopped(args.owner)
        return 2
    if args.domain:
        try:
            fleet.open_section(fleet.BACKLOG_DIR / f"{args.domain}-backlog.md")
        except Exception as error:
            print(f"no backlog {args.domain} · {error}", flush=True)
            return 2
        if not args.agent and not args.pr and written_before_domain(load(args.state) or {}, STATE_SOURCE):
            print(f"old state {args.state} · it was written before --domain, so it does not say what the last "
                  "watcher watched. Arm once with --domain and the --agent and --pr arguments the last watcher "
                  "used, then with --domain alone. Without that list, name the delegates and PRs in your "
                  "backlog's Open section, and drop any that `no agent` reports.", flush=True)
            return 2
    os.makedirs(os.path.dirname(args.state), exist_ok=True)
    lock = take_state_lock(args.state)
    if isinstance(lock, str):
        print(f"locked {lock}", flush=True)
        return 2
    signal.signal(signal.SIGTERM, print_stopped_and_exit)
    stop_waiting = declare_waiting()

    saved = load(args.state) or {}
    agents_seen = saved.get("agents", {}) if saved.get("source") == STATE_SOURCE else {}
    prs_seen = saved.get("prs", {})
    events = saved.get("pending", [])
    last_wake_at = saved.get("last_wake_at", 0)
    domain_agents, domain_prs = set(), set()
    if args.domain:
        domain_agents = set(saved.get("watched_agents", []))
        domain_prs = {slug_spec(spec, prs_seen) for spec in saved.get("watched_prs", [])} - {None}
        for spec in domain_prs:
            adopt_earlier_pr(prs_seen, spec)
    due_fired = saved.get("due_fired") if isinstance(saved.get("due_fired"), list) else []
    last_agents = None
    owner_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    started_at = time.time()
    agents_ok_at = started_at
    backlog_ok_at = started_at
    github_ok_at = {}
    clock = MacClock(saved.get("clock"))
    owner_misses = 0
    name_misses = {name: 0 for name in args.agent if not agents_seen.get(name, {}).get("present")}
    next_pr_poll = 0
    next_alive_at = started_at + ALIVE_SECONDS
    first_event_at = None
    first_urgent_at = None

    def watched_names():
        return list(dict.fromkeys(args.agent + sorted(domain_agents)))

    def watched_specs():
        return list(dict.fromkeys(args.pr + sorted(domain_prs)))

    def save_state(pending):
        save(args.state, {"source": STATE_SOURCE, "agents": agents_seen, "prs": prs_seen, "pending": pending,
                          "last_wake_at": last_wake_at, "clock": clock.reading(),
                          "watched_agents": watched_names(), "watched_prs": watched_specs(),
                          "due_fired": due_fired})

    def end(line):
        save_state(events)
        print(line, flush=True)
        return 0

    while True:
        clock.read()
        if parent_shell_gone():
            return end("parent gone")

        agents = live_agents()
        if agents is None:
            if clock.awake_seconds_since(agents_ok_at) >= BLIND_SECONDS:
                return end(f"blind hc · hc ls failed for {BLIND_SECONDS // 60} minutes")
        else:
            agents_ok_at = time.time()
            last_agents = agents
            if args.owner:
                owner_misses = 0 if owner_present(agents, args.owner, owner_session) else owner_misses + 1
                if owner_misses >= OWNER_MISSES_BEFORE_GONE:
                    return end("owner gone")
            ended = ended_delegates()
            listed = set(named(agents))
            for name in list(name_misses):
                if name in listed or was_ended(name, {}, ended):
                    del name_misses[name]
                else:
                    name_misses[name] += 1
            never_listed = [name for name, misses in name_misses.items() if misses >= NAME_MISSES_BEFORE_UNKNOWN]
            if never_listed:
                for name in never_listed:
                    print(f"no agent {name}", flush=True)
                return 2
            if args.domain:
                members, backlog_failure = domain_member_names(agents, args.domain)
                if members is not None:
                    backlog_ok_at = time.time()
                    domain_agents = watched_agent_names(members, under_watch_names(domain_agents, agents, agents_seen),
                                                        agents_seen, listed)
                    domain_agents -= given_terminals(args.agent, agents)
                elif clock.awake_seconds_since(backlog_ok_at) >= BLIND_SECONDS:
                    return end(f"blind backlog · the {args.domain} backlog could not be read for "
                               f"{BLIND_SECONDS // 60} minutes · {backlog_failure}")
            now_agents = agent_snapshot(watched_names(), agents, agents_seen, clock, ended)
            events += agent_events(agents_seen, now_agents)
            agents_seen = merged(agents_seen, now_agents)
            copy_to_same_terminal(agents_seen, now_agents, agents)

        if time.time() >= next_pr_poll:
            searched = {}
            if args.domain and last_agents is not None:
                try:
                    named_prs, open_numbers, search_failed, backlog_text = domain_pull_requests(args.domain, last_agents)
                except Exception:
                    named_prs = None
                if named_prs is not None:
                    searched = {**{slug: None for slug in open_numbers}, **search_failed}
                    for spec in named_prs | domain_prs:
                        adopt_earlier_pr(prs_seen, spec)
                    also_given = {slug_spec(spec, prs_seen) for spec in args.pr} - {None}
                    domain_prs = watched_pr_specs(named_prs, domain_prs, open_numbers, search_failed, prs_seen,
                                                  backlog_text) - also_given
            specs = watched_specs()
            for spec in specs:
                adopt_earlier_pr(prs_seen, spec)
            now_prs, failed = pr_snapshot(specs)
            failed = {**{slug: why for slug, why in searched.items() if why}, **failed}
            events += pr_events(prs_seen, now_prs)
            prs_seen = merged(prs_seen, now_prs)
            copy_to_same_pr(prs_seen, now_prs)
            next_pr_poll = time.time() + PR_POLL_SECONDS
            if args.domain:
                lines, due_fired = due_lines(args.domain, time.strftime("%Y-%m-%d"), due_fired)
                events += lines or []
            for repo in dict.fromkeys([spec.rsplit("#", 1)[0] for spec in specs] + list(searched)):
                if repo not in failed:
                    github_ok_at[repo] = time.time()
                elif clock.awake_seconds_since(github_ok_at.get(repo, started_at)) >= BLIND_SECONDS:
                    return end(f"blind {os.path.basename(repo)} · GitHub queries failed for {BLIND_SECONDS // 60} minutes"
                               f" · {failed[repo]}")

        save_state(events)

        now = time.time()
        if events and first_event_at is None:
            first_event_at = now
        if first_urgent_at is None and any(URGENT.search(line) for line in events):
            first_urgent_at = now
        wake_at = wake_time(first_event_at, first_urgent_at, last_wake_at)
        if wake_at is not None and now >= wake_at:
            for line in events:
                print(line, flush=True)
            last_wake_at = now
            save_state([])
            try:
                for line in ended_lines(events, prs_seen, ended_delegates()):
                    print(line, flush=True)
            except Exception:
                pass
            if args.owner:
                print_rules(agents, args.owner, stop_waiting)
            return 0
        if now >= next_alive_at:
            print("alive", flush=True)
            next_alive_at = now + ALIVE_SECONDS
        time.sleep(AGENT_POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())

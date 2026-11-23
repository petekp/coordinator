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

  ask <agent> <count> · <text>   its inbox holds more questions and tasks for the user
  gone <agent>                   no live terminal runs it any more
  unknown <agent>                its terminal has reported no status for 5 minutes
                                 with the Mac awake
  settled <agent> blocked        stopped on a dialog or a question
  settled <agent> error          stopped on an error: read its terminal
                                 A settled line ends with · <n> findings in
                                 its inbox when its saved inbox holds open
                                 findings.
  pr <repo>#<n> merged|closed    the PR left the open state
  pr <repo>#<n> checks failure   checks finished and failed
  pr <repo>#<n> review changes_requested
  pr <repo>#<n> not found        GitHub has no such PR: fix the --pr argument

Other events:

  settled <agent> idle|done      finished a turn. A compaction while idle is
                                 not a turn and prints nothing.
  report <agent> <path> · <n> questions, <n> issues, <n> findings
                                 its REPORT.md changed and it has settled.
                                 The counts are the list entries under the
                                 report's "Questions for the user", "Issues
                                 filed" and "Findings" headings, for each
                                 heading it has; a report with none of them
                                 gets no counts. Then · <n> findings in its
                                 inbox, as for settled.
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
  issue <number> · <title> · <url>
                                 with --domain: an open issue was created in
                                 a repo whose checkout the backlog's Boundary
                                 names, and neither the backlog nor an open
                                 item in the coordinator's saved inbox names
                                 it. watch_issues.py says how. Each issue
                                 prints once.

Lines that end the watch without an event:

  blind hc|backlog|<repo> · <reason>
                                 that source failed for 15 minutes with the Mac
                                 awake, or 60 for a GitHub network error: arm again
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
  rule · <text>                  a standing rule and its command: delegates
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
COMMAND_TIMEOUT_SECONDS = 60
OWNER_MISSES_BEFORE_GONE = 2
NAME_MISSES_BEFORE_UNKNOWN = 2
URGENT = re.compile(r"^(ask|gone|unknown|owner|pr \S+ (merged|closed|checks failure|review changes_requested|not found))"
                    r"|^settled \S+ (blocked|error)\b")
STATE_SOURCE = "hc"
RULE_SECONDS = 20

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import backlog  # noqa: E402
import fleet  # noqa: E402
from due_steps import due_lines  # noqa: E402
from mac_clock import MacClock  # noqa: E402
from update_check import update_line  # noqa: E402
from waiting_label import declare_waiting  # noqa: E402
from watch_cache import (AgentCache, StateSaver, failures_carried_over, github_blind_line,  # noqa: E402
                         load_json as load, pr_poll_delay, prune_state, save_json_atomic as save)
from watch_agents import agent_events, agent_snapshot, ended_delegates, was_ended  # noqa: E402
from watch_issues import domain_issue_sources, issue_lines, open_issues_since  # noqa: E402
from watch_domain import (adopt_earlier_pr, copy_to_same_pr, copy_to_same_terminal, domain_member_names,  # noqa: E402
                          domain_pull_requests, given_terminals, named, slug_spec, under_watch_names,
                          watched_agent_names, watched_pr_specs, written_before_domain)
from watch_lock import take_state_lock  # noqa: E402
from watch_prs import ended_lines, pr_events, pr_snapshot  # noqa: E402


def live_agents(cache=None):
    """Every live agent, or None when herdr-core could not be read."""
    try:
        return cache.load() if cache is not None else fleet.load_agents()
    except Exception:
        return None


class RulesTimeout(BaseException):
    pass


def rule_lines(domain):
    """Standing rules for the domain's coordinator, each with its command."""
    herd = fleet.Fleet()
    if domain not in herd.backlogs:
        return []
    lines = []
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
    issues_seen = saved.get("issues") if isinstance(saved.get("issues"), dict) else {}
    last_agents = None
    owner_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    started_at = time.time()
    agents_ok_at = started_at
    backlog_ok_at = started_at
    github_failing_since = failures_carried_over(saved, started_at)
    clock = MacClock(saved.get("clock"))
    owner_misses = 0
    name_misses = {name: 0 for name in args.agent if not agents_seen.get(name, {}).get("present")}
    agent_cache = AgentCache()
    state_saver = StateSaver(args.state)
    gh_failures = 0
    last_open_numbers = {}
    next_pr_poll = 0
    next_alive_at = started_at + ALIVE_SECONDS
    first_event_at = None
    first_urgent_at = None

    def watched_names():
        return list(dict.fromkeys(args.agent + sorted(domain_agents)))

    def watched_specs():
        return list(dict.fromkeys(args.pr + sorted(domain_prs)))

    def save_state(pending, force=False):
        state_saver.save({"source": STATE_SOURCE, "agents": agents_seen, "prs": prs_seen, "pending": pending,
                          "last_wake_at": last_wake_at, "clock": clock.reading(),
                          "watched_agents": watched_names(), "watched_prs": watched_specs(),
                          "due_fired": due_fired, "issues": issues_seen,
                          "github_failing_since": github_failing_since}, force=force)

    def end(line):
        save_state(events, force=True)
        print(line, flush=True)
        return 0

    while True:
        clock.read()
        if parent_shell_gone():
            return end("parent gone")

        agents = live_agents(agent_cache)
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
                    last_open_numbers = open_numbers
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
            issue_repos = []
            if args.domain and last_agents is not None:
                try:
                    issue_repos, backlog_text, inbox_text = domain_issue_sources(args.domain, last_agents)
                    lines, issues_now, issues_failed = issue_lines(issue_repos, issues_seen, time.time(),
                                                                   open_issues_since, backlog_text, inbox_text)
                except Exception:
                    issue_repos = []
                else:
                    issues_seen = issues_now
                    events += lines
                    failed = {**issues_failed, **failed}
            events += pr_events(prs_seen, now_prs)
            prs_seen = merged(prs_seen, now_prs)
            copy_to_same_pr(prs_seen, now_prs)
            prune_state(agents_seen, prs_seen, set(watched_names()), set(specs), last_agents, last_open_numbers)
            gh_failures = gh_failures + 1 if failed else 0
            next_pr_poll = time.time() + pr_poll_delay(gh_failures, PR_POLL_SECONDS)
            if args.domain:
                lines, due_fired = due_lines(args.domain, time.strftime("%Y-%m-%d"), due_fired)
                events += lines or []
            for repo in dict.fromkeys([spec.rsplit("#", 1)[0] for spec in specs] + list(searched) + sorted(issue_repos)):
                blind = github_blind_line(repo, failed.get(repo), github_failing_since, clock, BLIND_SECONDS)
                if blind:
                    return end(blind)

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
            prune_state(agents_seen, prs_seen, set(watched_names()), set(watched_specs()), last_agents, last_open_numbers)
            save_state([], force=True)
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

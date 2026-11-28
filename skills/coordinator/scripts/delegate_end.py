#!/usr/bin/env python3
"""
End a delegate that has gone quiet, and resume it when something arrives.

  delegate_end.py --list-idle       your delegates quiet for a day or more
  delegate_end.py <agent>           end one of your delegates
  delegate_end.py --resume <agent>  bring an ended delegate back

Run it from the coordinator's terminal. Ending stops the delegate's Claude
Code process and removes its herdr-core terminal, which closes its tab. Its
worktree, branch, backlog line and transcript stay, and so do the questions,
tasks and findings in its inbox. fleet_status.py keeps listing it, as ended. The record in
<state_dir>/ended/, named by its session id, holds what resuming
needs, with the text its terminal showed.

A delegate whose turn ended while a background task it started still runs counts as
idle. Ending it stops that task with the session.

Ending refuses a delegate that is working, one stopped on a dialog, and one
that is not yours. It also refuses:

  quiet under a day, or no model response found in its transcript
                           pass --now to end it anyway
  items in its inbox       questions, tasks and open findings. Relay each to the
                           user in your own inbox, or close the ones already
                           settled, then pass --relayed

Resuming starts the same session in a new terminal and tab with the same
name and tab label, in the delegate's folder, with the delegate flags. It
runs the cap check first, since a resumed delegate counts toward your three.
Send the delegate its work afterwards.

Exit status: 0 done, 1 refused, 2 the command could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import fleet  # noqa: E402
import live_without_status  # noqa: E402
import inbox_state  # noqa: E402
from background_tasks import is_working  # noqa: E402
from fleet import CheckError, age, run  # noqa: E402

THIS_SCRIPT = f"{fleet.SCRIPTS}/delegate_end.py"


class Refused(Exception):
    pass


def quiet_since(agent: dict, now: dt.datetime) -> dt.datetime | None:
    return fleet.last_response(fleet.session_of(agent), now)


def list_idle(herd: fleet.Fleet, domain: str, now: dt.datetime) -> int:
    if domain in fleet.DOMAINS_KEPT_RUNNING:
        print(f"{domain} is in keep_running, so its delegates are not ended.")
        return 0
    rows = []
    for member in herd.members_of(domain):
        agent = member["agent"]
        if agent["status"] == "blocked" or is_working(agent):
            continue
        since = quiet_since(agent, now)
        if since is None or now - since < fleet.IDLE_BEFORE_ENDING:
            continue
        rows.append((since, agent.get("name") or agent["address"], member["task"], len(inbox_state.inbox_items(agent))))
    if not rows:
        print("No delegate of yours has been quiet for a day.")
        return 0
    print(f"Quiet for a day or more: {len(rows)} of your delegates. End each with:")
    for since, name, task, items in sorted(rows):
        relay = f"   its inbox holds {items} item{'s' if items > 1 else ''}: relay them first, then add --relayed" if items else ""
        print(f"  {THIS_SCRIPT} {name}   # [{task}] quiet {age(since, now)}{relay}")
    print("Each keeps its worktree, branch and backlog line. Resume one with --resume <agent> "
          "when a review, an answer or a check result arrives for it.")
    return 0


def end(herd: fleet.Fleet, domain: str, target: str, now: dt.datetime, relayed: bool, end_now: bool) -> int:
    if domain in fleet.DOMAINS_KEPT_RUNNING:
        raise Refused(f"the user chose to keep {domain}'s delegates running")
    member = next(
        (m for m in herd.members_of(domain) if target in (m["agent"].get("name"), m["agent"]["address"])), None
    )
    if member is None:
        raise Refused(f"{target} is not a live delegate of {domain}; only its own coordinator ends a delegate")
    agent, task = member["agent"], member["task"]
    name = agent.get("name") or fleet.session_of(agent)[:fleet.SHORT_SESSION_ID]
    if is_working(agent):
        raise Refused(f"{name} is working")
    if agent["status"] == "blocked":
        raise Refused(f"{name} is stopped on a dialog or a question, which is the user's to answer")
    since = quiet_since(agent, now)
    if since is None and not end_now:
        raise Refused(f"no model response found in {name}'s transcript, so how long it has been quiet is unknown; "
                      "pass --now to end it anyway")
    if since is not None and now - since < fleet.IDLE_BEFORE_ENDING and not end_now:
        raise Refused(f"{name} responded {age(since, now)} ago, under a day; pass --now to end it anyway")
    items = inbox_state.inbox_items(agent)
    if items and not relayed:
        listed = "\n".join(f"  {item}" for item in items)
        raise Refused(f"{name}'s inbox holds {len(items)} item{'s' if len(items) > 1 else ''}:\n{listed}\n"
                      "Relay each question, task and finding to the user in your own inbox, or close the ones "
                      "already settled, then pass --relayed")

    slug, branch, _, _ = fleet.repo_of(agent["cwd"], herd.checkouts)
    terminal_text = subprocess.run(
        ["hc", "read", "--lines", "80", agent["address"]],
        capture_output=True, text=True, timeout=fleet.COMMAND_TIMEOUT_SECONDS,
    ).stdout
    terminal_info = fleet.terminal_or_none(agent["address"]) or {}
    terminal_labels = terminal_info.get("labels") or {}
    tab_label = agent["tab_label"] or f"{domain} [{task}]"
    record = dict(
        name=name, task=task, domain=domain, tab_label=tab_label, session=fleet.session_of(agent),
        program=agent["program"], model=terminal_labels.get("fleet.model"),
        effort=terminal_labels.get("fleet.effort"),
        cwd=agent["cwd"], repo=slug, branch=branch, ended_at=now.isoformat(),
        quiet_since=since.isoformat() if since else None, terminal_text=terminal_text,
    )
    if not record["session"]:
        raise CheckError(f"no session id found for {name}, so it could not be resumed. To end it anyway, run "
                         f"`hc kill {agent['address']}`, then `hc rm {agent['address']}`")
    fleet.ENDED_DIR.mkdir(parents=True, exist_ok=True)
    path = fleet.ended_record_path(record["session"])
    path.write_text(json.dumps(record, indent=1))
    try:
        fleet.end_terminal(agent["address"])
    except CheckError as error:
        if any(fleet.session_of(a) == record["session"] for a in fleet.load_agents()):
            path.unlink()
            raise CheckError(f"{name} still runs, so it was not ended: {error}")
        print(f"{name}'s session ended, but its terminal record stayed: {error}")
    stopped = f" Its leftover tasks stopped with it ({agent['still_running']})." if agent.get("still_running") else ""
    print(f"ended {name}.{stopped} Its worktree, branch and backlog line stay. Resume it with:\n"
          f"  {THIS_SCRIPT} --resume {name}")
    return 0


def resume(herd: fleet.Fleet, domain: str, name: str) -> int:
    named = sorted((r for r in fleet.ended_records() if r["name"] == name), key=lambda r: r["ended_at"])
    if not named:
        known = ", ".join(r["name"] for r in fleet.ended_records(domain)) or "none"
        raise Refused(f"no ended delegate named {name}. Ended in {domain}: {known}")
    record = named[-1]
    path = fleet.ended_record_path(record["session"])
    if record["domain"] != domain:
        raise Refused(f"{name} is {record['domain']}'s delegate; only its own coordinator resumes it")
    if any(fleet.session_of(a) == record["session"] for a in herd.agents):
        path.unlink()
        print(f"{name}'s session is already running; the ended record was removed")
        return 0
    if not Path(record["cwd"]).is_dir():
        raise Refused(f"{name}'s folder is gone: {record['cwd']}. Its session is {record['session']}")
    cap = subprocess.run([sys.executable, str(Path(__file__).with_name("delegate_cap.py")), "--resuming", name],
                         capture_output=True, text=True)
    if cap.returncode != 0:
        print(cap.stdout + cap.stderr, end="")
        raise Refused("the cap check did not allow another working delegate")

    coordinator = herd.coordinator_of(domain)
    if not coordinator.get("space"):
        raise CheckError(f"terminal {coordinator['address']} has no herdr.space label, so the new tab has no space to open in")
    if fleet.find_agent(herd.agents, name):
        raise Refused(f"another live agent is already named {name}")
    task = record.get("task") or name
    tab_label = record.get("tab_label") or f"{domain} [{task}]"
    if record.get("program") == "codex":
        raise Refused(f"{name}'s ended record is a Codex session, which delegate_end.py no longer supports. "
                      f"Resume manually with `codex resume {record['session']}`")
    saved_model = record.get("model")
    saved_effort = record.get("effort")
    model_flags = ["--model", saved_model] if saved_model and not record.get("quiet_since") else []
    effort_flags = ["--effort", saved_effort] if saved_effort else []
    program = [fleet.CLAUDE, "--resume", record["session"], *model_flags, *effort_flags,
               *fleet.DELEGATE_FLAGS, "--name", tab_label]
    labels = {"fleet.domain": domain, "fleet.task": task}
    if saved_model:
        labels["fleet.model"] = saved_model
    if saved_effort:
        labels["fleet.effort"] = saved_effort
    terminal = fleet.launch(
        name=name, cwd=record["cwd"], tab_label=tab_label, space=coordinator["space"],
        labels=labels,
        program=config.with_delegate_env(program), parent=coordinator["address"],
    )
    if not (terminal["running"] or live_without_status.process_alive(terminal["address"])):
        raise CheckError(f"no agent showed in terminal {terminal['address']} within {fleet.SECONDS_TO_WAIT_FOR_AGENT} "
                         f"seconds, so the ended record stays. It shows:\n{fleet.terminal_text(terminal['address'])}\n"
                         f"Remove a finished terminal with `hc rm {terminal['address']}` before trying again")
    path.unlink()
    if not terminal["running"]:
        print(live_without_status.note(terminal["address"]))
    print(f"resumed {name} as terminal {terminal['address']}, tab \"{tab_label}\". Send it the work with:\n"
          f"  {fleet.SCRIPTS}/delegate_cap.py --to {name} --send \"<what arrived>\"")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent", nargs="?", help="the delegate's name or terminal address")
    parser.add_argument("--list-idle", action="store_true", help="list your delegates quiet for a day or more")
    parser.add_argument("--resume", action="store_true", help="resume the ended delegate")
    parser.add_argument("--relayed", action="store_true",
                        help="its inbox's questions, tasks and findings are relayed or settled")
    parser.add_argument("--now", action="store_true", help="end it though it responded within the last day")
    args = parser.parse_args()
    if not args.list_idle and not args.agent:
        parser.error("name a delegate, or pass --list-idle")

    try:
        herd = fleet.Fleet()
        domain = herd.own_domain()
        now = dt.datetime.now(dt.timezone.utc)
        if args.list_idle:
            return list_idle(herd, domain, now)
        if args.resume:
            return resume(herd, domain, args.agent)
        return end(herd, domain, args.agent, now, args.relayed, args.now)
    except Refused as refusal:
        print(f"REFUSED  {refusal}")
        return 1
    except (CheckError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"delegate_end: could not run: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

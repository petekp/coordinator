#!/usr/bin/env python3
"""
Start, restart and stop coordinators in the Herdr app.

  coordinator_restart.py ask <domain>... | --all   ask coordinators to take their Restart steps
  coordinator_restart.py restart <domain>          exit one that replied ready; start a new one in its terminal
  coordinator_restart.py restart <domain> --terminal <address>
                                                   replace whatever session runs in that terminal
  coordinator_restart.py stop <domain>             exit one that replied ready and leave its terminal a shell
  coordinator_restart.py start <domain> --cwd <path> [--space <space id>] [--name <tab name>]
                                                   start one in a new tab, for a domain that has none running
  coordinator_restart.py start <domain> --cwd <path> --in <address>
                                                   start one in a terminal that holds only a shell

Run it when the user asks. A restart gives the coordinator a new Claude Code
process, so it loads the installed Claude Code and the current skill text.
The new session starts in the same terminal, so its address, its tab and
its delegates' link to it stay. When that terminal ended with its session,
the new one starts in a new terminal in the same tab. Its conversation and
its inbox do not carry over: the backlog does, which is why `ask` comes
first. `ask` sends the coordinator the user's reason and the Restart steps from
SKILL.md, and the coordinator replies "ready for restart" when its backlog
is committed and its watcher is stopped.

A coordinator is found by its terminal's labels, fleet.role=coordinator and
fleet.domain=<domain>. This command sets them on each terminal it starts.

A coordinator started in a new terminal gets the inbox mod's panel beside
it, when the inbox is installed. One restarted in its own terminal keeps
the panel it had.

`restart` and `stop` refuse a coordinator that is working, one that has not
replied "ready for restart", one whose backlog or second file has uncommitted
changes, and
one whose prompt box holds a draft. They type "/exit" as key presses and
stop if the session is still there 40 seconds later, printing its screen: a
dialog there, such as "Background work is running", is the user's to answer.

A new coordinator is always a new session. Never resume one by name or by
re-running a terminal's last command: an older session with the same name
comes back holding state from before its last restart.

Exit status: 0 done, 1 refused, 2 the command could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet  # noqa: E402
import inbox_state  # noqa: E402
from fleet import CheckError, run  # noqa: E402

READY = "ready for restart"
REPLY_MARK = "⏺"
PROMPT_MARK = "❯"
EXIT_COMMAND = "/exit"
SHELLS = {"zsh", "bash", "fish", "sh"}
SECONDS_TO_WAIT_FOR_EXIT = 40
SECONDS_TO_WAIT_FOR_START = 120
POLL_SECONDS = 2


class Refused(Exception):
    pass


def ask_text(reason: str) -> str:
    stamp = dt.datetime.now().astimezone().strftime("%m-%d %H:%M %Z")
    return (
        f"From coordinator_restart.py, {stamp}: the user asked for this coordinator to restart. {reason} "
        "This session will exit and a new one will start in this terminal. Take the Restart steps in your coordinator "
        "skill now: close each inbox question the backlog already answers, bring your backlog's Open section up to "
        "date in place, with every delegate handle and every open ask as an Ask: sentence with its options, add no "
        "new section, and commit it when Git tracks it, then stop your "
        f"fleet watcher, monitors and background tasks. Start nothing new. When that is done, reply with the single "
        f"line \"{READY}\"."
    )


def replied_ready(address: str) -> bool:
    text = run(["hc", "read", "--lines", "400", address])
    return any(line.strip().lower().startswith(f"{REPLY_MARK} {READY}") for line in text.splitlines())


def agent_in(address: str) -> dict | None:
    return next((a for a in fleet.load_agents() if a["address"] == address), None)


def listed(address: str) -> dict | None:
    """The live terminal as `hc ls` lists it."""
    return next((t for t in fleet.listed_terminals() if t["address"] == address), None)


def check_can_exit(herd: fleet.Fleet, domain: str, agent: dict, replacing: bool) -> None:
    address = agent["address"]
    if agent["status"] == "working":
        raise Refused(f"the session in terminal {address} is working")
    if agent["status"] == "blocked":
        raise Refused(f"the session in terminal {address} is stopped on a dialog or a question, which is the user's to answer")
    if not replacing and not replied_ready(address):
        raise Refused(f"the {domain} coordinator has not replied \"{READY}\"; run `ask {domain}` and wait for it")
    files = herd.files(domain)
    is_tracked = fleet.git(str(files.notes.parent), "rev-parse", "--git-dir") is not None
    for path in (files.notes, files.later):
        if is_tracked and path.exists() and fleet.git(str(path.parent), "status", "--porcelain", "--", str(path)):
            raise Refused(f"{path} has uncommitted changes, so the handoff is not saved")


def background_copies_since(started_ms: float) -> list[str]:
    """Sessions Claude Code moved to the background after that moment."""
    try:
        listed = json.loads(run(["claude", "agents", "--json"]))
    except (CheckError, json.JSONDecodeError):
        return []
    return [entry.get("id") or entry.get("sessionId") for entry in listed
            if entry.get("kind") == "background" and (entry.get("startedAt") or 0) >= started_ms]


def prompt_box_text(address: str) -> str | None:
    """The text in the session's prompt box, or None when its screen shows no prompt box."""
    lines = run(["hc", "read", address]).splitlines()
    prompts = [line for line in lines if line.strip().startswith(PROMPT_MARK)]
    return prompts[-1].split(PROMPT_MARK, 1)[1].replace("\xa0", " ").strip() if prompts else None


def exit_session(agent: dict) -> None:
    """Type /exit into the session and wait for it to leave its terminal.

    A suggestion Claude Code shows in the box gives way to typed keys, and a
    draft does not, so the box holds exactly the command only when there was
    no draft.
    """
    address = agent["address"]
    asked_at_ms = time.time() * 1000
    run(["hc", "keys", address, *EXIT_COMMAND])
    typed = prompt_box_text(address)
    if typed != EXIT_COMMAND:
        run(["hc", "keys", address, *(["backspace"] * len(EXIT_COMMAND))])
        raise Refused(f"the prompt box in terminal {address} holds {typed!r} after typing {EXIT_COMMAND}, so someone "
                      "has a draft there; the typed command was removed")
    run(["hc", "keys", address, "enter"])
    deadline = time.time() + SECONDS_TO_WAIT_FOR_EXIT
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        terminal = listed(address)
        if terminal is None or not fleet.runs_an_agent(terminal):
            break
    else:
        screen = subprocess.run(["hc", "read", address], capture_output=True, text=True).stdout.strip()[-800:]
        raise CheckError(f"the session in terminal {address} did not exit within {SECONDS_TO_WAIT_FOR_EXIT} seconds. "
                         f"Its screen:\n{screen}")
    copies = background_copies_since(asked_at_ms)
    if copies:
        print(f"Claude Code moved a copy of the old session to the background: {', '.join(copies)}. "
              f"Ask the user before stopping it with `claude stop <id>`.")


def send_bare_text(address: str, text: str) -> None:
    """Type text and Enter into a terminal with no sender.

    From a terminal, `hc send` wraps the text in an <hc from="..."> tag, which
    a shell cannot run.
    """
    environment = {key: value for key, value in os.environ.items() if key != "HC_TERMINAL_ID"}
    sent = subprocess.run(["hc", "send", address, text], capture_output=True, text=True, env=environment,
                          timeout=fleet.COMMAND_TIMEOUT_SECONDS + 60)
    if sent.returncode != 0:
        raise CheckError(f"hc send {address}: {(sent.stderr or sent.stdout).strip()[:300]}")



# Coordinators are pinned to the top of the Herdr app's Agents list.
PINNED = {"herdr.pinned": "1"}


def coordinator_name(domain: str) -> str:
    """The tab and session name for a domain's coordinator: web-app becomes Web App."""
    return " ".join(word.capitalize() for word in domain.split("-") if word)


def start_in(address: str, domain: str, cwd: str, label: str) -> None:
    """Start a new coordinator session in `cwd`, in a terminal at a shell prompt or one whose agent has exited."""
    terminal = json.loads(run(["hc", "get", address, "--json"]))
    program = [fleet.CLAUDE, "--name", label, f"/coordinator {domain}"]
    if terminal.get("state") == "live":
        at_a_shell = (listed(address) or {}).get("agent") in SHELLS
        if not at_a_shell:
            raise Refused(f"terminal {address} is not at a shell prompt, so a command typed there would go to its program")
        run(["hc", "label", address, "fleet.role=coordinator", f"fleet.domain={domain}", *(f"{k}={v}" for k, v in PINNED.items())])
        send_bare_text(address, f"cd {shlex.quote(cwd)} && {shlex.join(program)}")
        report_started(address, domain)
        return
    labels = terminal.get("labels") or {}
    if not labels.get("herdr.space"):
        raise CheckError(f"terminal {address} ended and has no herdr.space label, so the new session has no space to open in")
    carried = {key: labels[key] for key in ("inbox.panel",) if key in labels}
    started = fleet.launch(
        name=None, cwd=cwd, tab_label=label, space=labels["herdr.space"], tab=labels.get("herdr.tab"),
        labels={"fleet.role": "coordinator", "fleet.domain": domain, **PINNED, **carried},
        program=program, parent=None,
    )
    run(["hc", "rm", address])
    open_inbox_panel(started["address"])
    report_started(started["address"], domain)


def open_inbox_panel(address: str) -> None:
    """Open the inbox mod's pane to the right of the terminal, unless it has one or the inbox is not installed.

    The inbox finds an open panel by the terminal's inbox.panel label, which
    holds the panel's pane id.
    """
    try:
        if inbox_state.inbox_store() is None:
            return
        terminal = fleet.terminal_or_none(address) or {}
        if (terminal.get("labels") or {}).get("inbox.panel"):
            return
        shown = json.loads(run(["herdr-app", "ls"]))
        pane = next((shown_terminal["pane"] for space in shown["spaces"] for tab in space["tabs"]
                     for shown_terminal in tab["terminals"] if shown_terminal["terminal"] == address), None)
        if pane is None:
            raise CheckError(f"the app shows no pane for terminal {address}")
        panel = json.loads(run(["herdr-app", "do", "pane.split", json.dumps(dict(direction="right", pane=pane, mod="inbox"))]))
        run(["hc", "label", address, f"inbox.panel={panel['pane']}"])
    except (CheckError, KeyError, ValueError) as error:
        print(f"The inbox panel was not opened beside terminal {address}: {error}. Typing /inbox in the session opens it.")


def report_started(address: str, domain: str) -> None:
    deadline = time.time() + SECONDS_TO_WAIT_FOR_START
    agent = None
    while time.time() < deadline:
        agent = agent_in(address)
        if agent and fleet.session_of(agent) and agent["status"]:
            break
        time.sleep(POLL_SECONDS)
    if not agent or not fleet.session_of(agent):
        raise CheckError(f"no Claude Code session showed in terminal {address} within {SECONDS_TO_WAIT_FOR_START} "
                         f"seconds. Read it with `hc read {address}` before anything else")
    session = fleet.session_of(agent)
    print(f"{domain} coordinator: new session {session[:8]} in terminal {address}, {agent['status']}, "
          f"Claude Code {fleet.claude_code_version(session)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask")
    ask.add_argument("domains", nargs="*")
    ask.add_argument("--all", action="store_true", help="every running coordinator but the one in this terminal")
    ask.add_argument("--reason", default="", help="the user's words, quoted")
    restart = commands.add_parser("restart")
    restart.add_argument("domain")
    restart.add_argument("--terminal", help="replace the session in this terminal, whoever it is")
    stop = commands.add_parser("stop")
    stop.add_argument("domain")
    start = commands.add_parser("start")
    start.add_argument("domain")
    start.add_argument("--cwd", required=True)
    start.add_argument("--name", help='the tab and session name; default: the domain in title case, as "Web App"')
    start.add_argument("--space", help="the Herdr app space id, from `herdr-app ls`; default: this terminal's space")
    start.add_argument("--in", dest="in_terminal", metavar="ADDRESS",
                       help="start in this terminal, which holds only a shell")
    args = parser.parse_args()

    try:
        herd = fleet.Fleet()
        own_address = os.environ.get("HC_TERMINAL_ID")
        if args.command == "ask":
            running = {herd.coordinated_domain(a): a for a in herd.agents if herd.coordinated_domain(a)}
            domains = [d for d, a in running.items() if a["address"] != own_address] if args.all else args.domains
            if not domains:
                parser.error("name a domain, or pass --all")
            for domain in domains:
                agent = running.get(domain)
                if agent is None:
                    print(f"{domain}: no coordinator running")
                    continue
                sent = subprocess.run(["hc", "send", agent["address"], ask_text(args.reason)], capture_output=True, text=True)
                print(f"{domain}: asked" if sent.returncode == 0
                      else f"{domain}: not asked: {(sent.stderr or sent.stdout).strip()[:200]}")
            return 0

        domain = args.domain
        if args.command != "start":
            herd.backlog(domain)
        label = getattr(args, "name", None) or coordinator_name(domain)
        if args.command == "start":
            if herd.coordinator_of(domain):
                raise Refused(f"a coordinator for {domain} is already running")
            cwd = os.path.realpath(os.path.expanduser(args.cwd))
            if not os.path.isdir(cwd):
                raise CheckError(f"no folder at {cwd}")
            if args.in_terminal:
                if fleet.find_agent(herd.agents, args.in_terminal):
                    raise Refused(f"terminal {args.in_terminal} runs an agent; use `restart {domain} --terminal`")
                start_in(args.in_terminal, domain, cwd, label)
                return 0
            own = fleet.find_agent(herd.agents, own_address or "")
            space = args.space or (own or {}).get("space")
            if not space:
                raise CheckError("pass --space <space id> from `herdr-app ls`; this terminal has no herdr.space label")
            terminal = fleet.launch(
                name=None, cwd=cwd, tab_label=label,
                space=space, labels={"fleet.role": "coordinator", "fleet.domain": domain, **PINNED},
                program=[fleet.CLAUDE, "--name", label, f"/coordinator {domain}"], parent=None,
            )
            open_inbox_panel(terminal["address"])
            report_started(terminal["address"], domain)
            return 0

        replacing = args.command == "restart" and bool(args.terminal)
        agent = fleet.find_agent(herd.agents, args.terminal) if replacing else herd.coordinator_of(domain)
        if agent is None and replacing:
            raise Refused(f"terminal {args.terminal} runs no agent; use `start`")
        if agent is None:
            raise Refused(f"no terminal is labelled as the {domain} coordinator; if a stale session sits in its "
                          "terminal, pass --terminal")
        if agent["address"] == own_address:
            raise Refused("this is your own terminal; a session cannot restart itself")
        label = agent["tab_label"] or label
        check_can_exit(herd, domain, agent, replacing)
        exit_session(agent)
        if args.command == "stop":
            print(f"The {domain} coordinator exited. Terminal {agent['address']} holds a shell. Start a new one with:\n"
                  f"  {fleet.SCRIPTS}/coordinator_restart.py start {domain} --cwd {agent['cwd']} --in {agent['address']}")
            return 0
        start_in(agent["address"], domain, agent["cwd"], label)
        return 0
    except Refused as refusal:
        print(f"REFUSED  {refusal}")
        return 1
    except (CheckError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"coordinator_restart: could not run: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

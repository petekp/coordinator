"""
What the coordinator scripts share: who the coordinators and their delegates
are, which repos a domain works in, and which delegates were ended.

The fleet runs in herdr-core terminals, read with `hc`. An agent is a live
terminal whose foreground program is Claude Code, or reports a status, as
coding agents do and shells do not. Each one carries its terminal address,
such as t7, which `hc send`, `hc read` and `hc get` take.

A coordinator is the agent whose terminal carries the labels
fleet.role=coordinator and fleet.domain=<domain>. Its backlog is
<backlog_dir>/<domain>-backlog.md.

The user's settings come from config.py.

A delegate belongs to a domain when its terminal carries
fleet.domain=<domain>, or carries no fleet.domain label and the backlog's
Open section names its session id. delegate_start.py sets the label.

An ended delegate is one its coordinator closed with delegate_end.py. Its
record in <state_dir>/ended/ holds what resuming it needs.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import pwd
import re
import subprocess
import time
import uuid
from pathlib import Path

from config import CONFIG, CONFIG_FILE

HOME = Path.home()
BACKLOG_DIR = Path(CONFIG["backlog_dir"]).expanduser()
BRIEFS_DIR = BACKLOG_DIR / "briefs"
STATE_DIR = Path(CONFIG["state_dir"]).expanduser()
ENDED_DIR = STATE_DIR / "ended"
WATCH_DIR = STATE_DIR / "watch"
SESSION_FILES = HOME / ".claude" / "sessions"
SCRIPTS = str(Path(__file__).resolve().parent)
DOMAINS_KEPT_RUNNING = set(CONFIG["keep_running"])
IDLE_BEFORE_ENDING = dt.timedelta(hours=24)
SECTION_HEADING = re.compile(r"^## (?!#)")
SHORT_SESSION_ID = 8
TRANSCRIPT_TAIL_BYTES = (2_000_000, 12_000_000)
COMMAND_TIMEOUT_SECONDS = 60
AGENT_PROGRAM = "claude-code"
CLAUDE = "claude"
HC_NOT_FOUND = 3
DELEGATE_FLAGS = [str(arg) for arg in CONFIG["delegate_claude_args"]]
TERMINAL_NAME = re.compile(r"[a-z0-9_-]{1,32}")
SECONDS_TO_WAIT_FOR_TAB = 15
SECONDS_TO_WAIT_FOR_AGENT = 60


class CheckError(Exception):
    pass


def run(command: list[str], cwd: str | None = None) -> str:
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckError(f"{command[0]}: {error}")
    if proc.returncode != 0:
        raise CheckError(f"{' '.join(command[:4])}: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def parse_time(stamp: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def age(since: dt.datetime | None, now: dt.datetime) -> str:
    if since is None:
        return "?"
    minutes = max((now - since).total_seconds(), 0) / 60
    if minutes < 60:
        return f"{minutes:.0f}m"
    if minutes < 60 * 48:
        return f"{minutes / 60:.0f}h"
    return f"{minutes / 60 / 24:.0f}d"


def session_of_process(pid: int | None) -> str:
    """The Claude Code session a process runs now, from the file Claude Code keeps for it."""
    try:
        return json.loads((SESSION_FILES / f"{pid}.json").read_text()).get("sessionId") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def session_in_arguments(argv: list[str]) -> str:
    for flag in ("--session-id", "--resume"):
        if flag in argv[:-1]:
            return argv[argv.index(flag) + 1]
    return ""


def as_agent(terminal: dict) -> dict:
    """One agent, from a terminal as `hc get` prints it."""
    labels = terminal.get("labels") or {}
    foreground = terminal.get("foreground") or {}
    status = terminal.get("status") or {}
    has_own_tab = bool(labels.get("herdr.tab"))
    return dict(
        address=terminal["address"],
        name=terminal.get("name"),
        status=status.get("state"),
        status_text=status.get("msg") or "",
        turn=terminal.get("turn"),
        cwd=foreground.get("cwd") or terminal.get("cwd") or "",
        parent=terminal.get("parent"),
        session=(session_of_process(foreground.get("pid")) or labels.get("fleet.session")
                 or labels.get("inbox.session") or session_in_arguments(foreground.get("argv") or [])),
        program=CLAUDE,
        role=labels.get("fleet.role"),
        domain=labels.get("fleet.domain"),
        task=labels.get("fleet.task"),
        tab_label=(labels.get("where") or "").split(" / ", 1)[-1] if has_own_tab else "",
        space=labels.get("herdr.space"),
        tab=labels.get("herdr.tab"),
        inbox_asks=int(labels.get("inbox.needs-you") or 0),
        inbox_status=labels.get("inbox.status") or "",
    )


def runs_an_agent(listed_terminal: dict) -> bool:
    """A terminal as `hc ls` lists it: its foreground program is Claude Code, or reports a status."""
    return listed_terminal.get("agent") == AGENT_PROGRAM or listed_terminal.get("status") is not None


def agent_started_from_its_shell(terminal: dict) -> bool:
    """`hc ls` lists such a terminal by its shell, with no status; `hc get` shows the agent in the foreground."""
    argv = (terminal.get("foreground") or {}).get("argv") or []
    return bool(argv) and os.path.basename(argv[0]) == CLAUDE


def listed_terminals() -> list[dict]:
    listed = json.loads(run(["hc", "ls", "--json"])).get("terminals")
    if listed is None:
        raise CheckError("hc ls returned no terminals field")
    return listed


def terminal_or_none(address: str) -> dict | None:
    """The terminal as `hc get` prints it, or None when herdr-core has no such terminal."""
    try:
        got = subprocess.run(["hc", "get", address, "--json"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckError(f"hc get {address}: {error}")
    if got.returncode == HC_NOT_FOUND:
        return None
    if got.returncode != 0:
        raise CheckError(f"hc get {address}: {(got.stderr or got.stdout).strip()[:300]}")
    return json.loads(got.stdout)


def load_agents(terminals_file: str | None = None) -> list[dict]:
    """Every live agent. terminals_file holds a JSON list of saved `hc get` output.

    A terminal that ends between the listing and its lookup is left out. Any
    other failed lookup stops the read, so a live agent is never taken for gone.
    """
    if terminals_file:
        return [as_agent(terminal) for terminal in json.loads(Path(terminals_file).read_text())]
    agents = []
    for listed in listed_terminals():
        terminal = terminal_or_none(listed["address"])
        if terminal is not None and (runs_an_agent(listed) or agent_started_from_its_shell(terminal)):
            agents.append(as_agent(terminal))
    return agents


def find_agent(agents: list[dict], target: str) -> dict | None:
    """The agent with that name or terminal address."""
    return next((a for a in agents if target in (a.get("name"), a["address"])), None)


def open_section(backlog: Path) -> str:
    if not backlog.is_file():
        raise CheckError(f"no backlog at {backlog}")
    body: list[str] | None = None
    in_fence = False
    for line in backlog.read_text().splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        is_heading = not in_fence and SECTION_HEADING.match(line)
        if body is None:
            if is_heading and line.rstrip() == "## Open":
                body = []
        elif is_heading:
            break
        else:
            body.append(line)
    if body is None:
        raise CheckError(f"{backlog} has no '## Open' section")
    return "\n".join(body)


def section_text(text: str, heading: str) -> str:
    body: list[str] | None = None
    for line in text.splitlines():
        if body is None:
            if line.rstrip() == heading:
                body = []
        elif SECTION_HEADING.match(line):
            break
        else:
            body.append(line)
    return "\n".join(body or [])


def names_session(text: str, agent: dict) -> bool:
    session_id = agent.get("session") or ""
    return len(session_id) >= SHORT_SESSION_ID and session_id[:SHORT_SESSION_ID] in text


def label_parts(label: str) -> tuple[str, str] | None:
    match = re.match(r"(.+?) \[(.+)\]$", label)
    return (match.group(1).strip().lower().replace(" ", "-"), match.group(2)) if match else None


def repo_of(cwd: str, cache: dict[str, tuple]) -> tuple[str | None, str | None, str | None, str | None]:
    """The repo's owner/name, the checked-out branch, the commit when no branch is
    checked out, and what stopped the lookup, for the Git checkout at cwd."""
    if cwd not in cache:
        slug = branch = commit = problem = None
        if not cwd or not Path(cwd).is_dir():
            problem = f"its folder is gone: {cwd or 'none recorded'}"
        elif git(cwd, "rev-parse", "--git-dir") is None:
            problem = f"not a Git checkout: {cwd}"
        else:
            remotes = (git(cwd, "remote") or "").split()
            remote = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
            url = git(cwd, "remote", "get-url", remote) if remote else None
            match = re.search(r"[:/]([^/:\s]+/[^/\s]+?)(?:\.git)?$", url or "")
            slug = match.group(1) if match else None
            if not slug:
                problem = f"no remote with an owner/name URL: {cwd}"
            branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
            if branch in (None, "HEAD"):
                branch, commit = None, git(cwd, "rev-parse", "HEAD")
                problem = problem or f"no branch checked out at {cwd}"
        cache[cwd] = (slug, branch, commit, problem)
    return cache[cwd]


def boundary_repos(text: str, cache: dict[str, tuple]) -> set[str]:
    """Repos of the checkouts the backlog's Boundary names by their top folder."""
    slugs = set()
    for path in set(re.findall(r"(?<![\w.])(?:~|/)[\w./-]+", section_text(text, "## Boundary"))):
        folder = os.path.realpath(os.path.expanduser(path.rstrip("./")))
        is_checkout = Path(folder).is_dir() and git(folder, "rev-parse", "--show-toplevel") == folder
        slug = repo_of(folder, cache)[0] if is_checkout else None
        if slug:
            slugs.add(slug)
    return slugs


def backlog_repos(text: str, cache: dict[str, tuple]) -> set[str]:
    """Repos the backlog links a pull request in, and the checkouts its Boundary names by their top folder."""
    return set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)/pull/\d+", text)) | boundary_repos(text, cache)


def names_pull_request(text: str, slug: str, number: int, needs_link: bool) -> bool:
    """A link to the pull request, or a bare #<number> when no other repo of the domain has that number open."""
    if re.search(rf"{re.escape(slug)}/pull/{number}(?!\d)", text):
        return True
    return not needs_link and bool(re.search(rf"(?<![\w/])#{number}(?!\d)", text))


class Fleet:
    """The live agents and the backlogs, read once."""

    def __init__(self, terminals_file: str | None = None, agents: list[dict] | None = None):
        self.agents = agents if agents is not None else load_agents(terminals_file)
        self.backlogs = {p.name.removesuffix("-backlog.md"): p for p in sorted(BACKLOG_DIR.glob("*-backlog.md"))}
        self.checkouts: dict[str, tuple] = {}

    def coordinated_domain(self, agent: dict) -> str | None:
        return agent.get("domain") if agent.get("role") == "coordinator" else None

    def own_domain(self) -> str:
        """The domain of the coordinator in the terminal this script runs in."""
        address = os.environ.get("HC_TERMINAL_ID")
        here = next((a for a in self.agents if a["address"] == address), None)
        domain = self.coordinated_domain(here) if here else None
        if not domain:
            raise CheckError(
                f"terminal {address or '(HC_TERMINAL_ID unset)'} is not labelled as a coordinator. A coordinator "
                'labels its own terminal once: hc label "$HC_TERMINAL_ID" fleet.role=coordinator fleet.domain=<domain>'
            )
        return domain

    def backlog(self, domain: str) -> Path:
        if domain not in self.backlogs:
            raise CheckError(f"no backlog for {domain} in {BACKLOG_DIR}")
        return self.backlogs[domain]

    def coordinator_of(self, domain: str) -> dict | None:
        return next((a for a in self.agents if self.coordinated_domain(a) == domain), None)

    def members_of(self, domain: str) -> list[dict]:
        """The domain's live delegates: agent, task, and what placed each one."""
        open_items = open_section(self.backlog(domain))
        members = []
        for agent in self.agents:
            bracketed = label_parts(agent["tab_label"])
            if agent.get("role") == "coordinator" or (bracketed and bracketed[1].lower() == "coordinator"):
                continue
            by_label = agent.get("domain") == domain
            by_backlog = names_session(open_items, agent)
            if by_label or (by_backlog and not agent.get("domain")):
                task = agent.get("task") or (bracketed[1] if bracketed else agent.get("name") or agent["address"])
                members.append(dict(agent=agent, task=task, by_label=by_label, by_backlog=by_backlog))
        return members

    def repos_worked_in(self, domain: str) -> set[str]:
        """Repos the domain's coordinator and live delegates have a folder in."""
        cwds = [m["agent"]["cwd"] for m in self.members_of(domain)]
        coordinator = self.coordinator_of(domain)
        if coordinator:
            cwds.append(coordinator["cwd"])
        return {slug for slug in (repo_of(cwd, self.checkouts)[0] for cwd in cwds) if slug}

    def repos_of(self, domain: str) -> set[str]:
        return self.repos_worked_in(domain) | backlog_repos(self.backlog(domain).read_text(), self.checkouts)


# herdr-core starts terminals with its own environment, not the user's. A login
# shell gives the session the PATH of the user's own terminals.
POSIX_SHELLS = {"zsh", "bash", "sh", "ksh", "dash"}
LOGIN_SHELL = pwd.getpwuid(os.getuid()).pw_shell or os.environ.get("SHELL") or ""
SHELL = LOGIN_SHELL if os.path.basename(LOGIN_SHELL) in POSIX_SHELLS else "/bin/sh"
LOGIN_SHELL_THAT_BECOMES_ITS_COMMAND = [SHELL, "-lic", 'exec "$@"', SHELL]
LOGIN_SHELL_THAT_OUTLIVES_ITS_COMMAND = [SHELL, "-lic", '"$@"; exec "$0" -li', SHELL]


def launch(name: str | None, cwd: str, tab_label: str, space: str, labels: dict[str, str], program: list[str],
           parent: str | None, tab: str | None = None) -> dict:
    """Start a coding agent in a new herdr-core terminal shown in the Herdr app.

    program is the agent's command and its arguments, such as ["claude",
    "--model", "opus"]. With a parent, the app lists the new agent under that
    terminal's agent and the terminal keeps running when the parent's program
    ends, and ends when the agent exits. Without one, the terminal keeps a
    shell when the agent exits. The
    terminal joins the tab with that id, or gets a new one, and the tab is
    named tab_label. Returns the new terminal as `hc get` prints it, with
    `running` false when no agent showed in it: `hc run` returns before
    the agent starts, and the terminal ends at once when it cannot.
    """
    if name is not None and not TERMINAL_NAME.fullmatch(name):
        raise CheckError(f"{name!r} is not a terminal name: 1 to 32 of a-z, 0-9, - and _")
    tab = tab or f"tb{uuid.uuid4().hex}"
    all_labels = {"herdr.space": space, "herdr.tab": tab, **labels}
    lineage = ["--parent", parent, "--outlive-parent", "--no-notify"] if parent else ["--no-parent"]
    command = ["hc", "run", "--bg", "--json", *(["--name", name] if name else []), "--cwd", cwd, *lineage]
    for key, value in all_labels.items():
        command += ["--label", f"{key}={value}"]
    shell = LOGIN_SHELL_THAT_BECOMES_ITS_COMMAND if parent else LOGIN_SHELL_THAT_OUTLIVES_ITS_COMMAND
    started = json.loads(run([*command, "--", *shell, *program]))
    try:
        terminal = terminal_or_none(started["address"]) or dict(started, labels=all_labels)
    except CheckError:
        terminal = dict(started, labels=all_labels)
    terminal["tab_named"] = name_tab(tab, tab_label)
    terminal["running"] = agent_showed_in(started["address"])
    return terminal


def agent_showed_in(address: str) -> bool:
    deadline = time.time() + SECONDS_TO_WAIT_FOR_AGENT
    while time.time() < deadline:
        try:
            listed = next((t for t in listed_terminals() if t["address"] == address), None)
        except CheckError:
            listed = {}
        if listed is None:
            return False
        if runs_an_agent(listed):
            return True
        time.sleep(1)
    return False


def terminal_text(address: str, lines: int = 40) -> str:
    """What a live or finished terminal shows, for a failure report."""
    try:
        read = subprocess.run(["hc", "read", "--lines", str(lines), address], capture_output=True, text=True,
                              timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "\n".join(line for line in read.stdout.splitlines() if line.strip())[-1500:]


def name_tab(tab: str, tab_label: str) -> bool:
    """Name a Herdr app tab. The app takes about two seconds to show a tab for a new terminal."""
    deadline = time.time() + SECONDS_TO_WAIT_FOR_TAB
    while time.time() < deadline:
        try:
            renamed = subprocess.run(["herdr-app", "do", "tab.rename", json.dumps(dict(id=tab, name=tab_label))],
                                     capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if renamed.returncode == 0:
            return True
        time.sleep(0.5)
    return False


def end_terminal(address: str) -> None:
    """End a terminal's program, if it still runs, and remove its record.

    The app closes a tab when its last terminal ends.
    """
    try:
        subprocess.run(["hc", "kill", address], capture_output=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckError(f"hc kill {address}: {error}")
    run(["hc", "rm", address])


def keep_scratch_out_of_git(cwd: str) -> bool:
    """Make Git ignore the .scratch folder that holds a delegate's brief and report. True when it changed.

    The rule goes in the repo's info/exclude, which is not committed.
    """
    if git(cwd, "rev-parse", "--git-dir") is None or git(cwd, "check-ignore", "-q", ".scratch/BRIEF.md") is not None:
        return False
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    exclude = Path(common) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.is_file() else ""
    exclude.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + ".scratch/\n")
    return True


def session_of(agent: dict) -> str:
    return agent.get("session") or ""


def claude_code_version(session_id: str) -> str:
    for path in SESSION_FILES.glob("*.json"):
        try:
            saved = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if saved.get("sessionId") == session_id:
            return saved.get("version") or "?"
    return "?"


def claude_transcript(session_id: str) -> str | None:
    paths = glob.glob(str(HOME / f".claude/projects/*/{session_id}.jsonl")) if session_id else []
    return max(paths, key=os.path.getmtime) if paths else None


def transcript_of(session_id: str) -> str | None:
    return claude_transcript(session_id)


def program_of(session_id: str) -> str:
    """Which agent the session belongs to."""
    return CLAUDE


def lines_from_the_end(path: str):
    """The file's last lines, then a longer tail for a caller that found no match: (lines, holds the whole file)."""
    size = os.path.getsize(path)
    for tail in TRANSCRIPT_TAIL_BYTES:
        with open(path, "rb") as handle:
            handle.seek(max(0, size - tail))
            lines = handle.read().decode("utf-8", "replace").splitlines()
        yield lines, size <= tail
        if size <= tail:
            return


def last_response(session_id: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """When the model last responded in this session, read from the end of its transcript.

    A compaction writes no model response, so it does not move this time.
    """
    path = claude_transcript(session_id)
    if not path:
        return None
    for lines, _ in lines_from_the_end(path):
        for line in reversed(lines):
            if '"type":"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") or {}
            if not entry.get("timestamp") or not message.get("usage") or message.get("model") in (None, "<synthetic>"):
                continue
            when = parse_time(entry["timestamp"])
            if now is None or when <= now:
                return when
    return None


def transcript_marks(session_id: str) -> tuple[str | None, str | None]:
    """The times of the session's last assistant entry and of a compaction after it, if there is one.

    An idle compaction writes a compact_boundary entry and no assistant entry.
    """
    path = claude_transcript(session_id)
    if not path:
        return None, None
    for lines, holds_whole_file in lines_from_the_end(path):
        compacted = None
        for line in reversed(lines):
            is_boundary = '"compact_boundary"' in line
            if not is_boundary and '"type":"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "assistant" and entry.get("timestamp"):
                return entry["timestamp"], compacted
            if entry.get("subtype") == "compact_boundary" and compacted is None:
                compacted = entry.get("timestamp")
        if holds_whole_file:
            return None, compacted
    return None, None


def ended_records(domain: str | None = None) -> list[dict]:
    records = []
    for path in sorted(ENDED_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        is_whole = isinstance(record, dict) and all(
            isinstance(record.get(key), str) and record[key] for key in ("name", "session", "domain", "cwd", "ended_at")
        )
        if is_whole and (domain is None or record["domain"] == domain):
            records.append(record)
    return records


def ended_record_path(session_id: str) -> Path:
    return ENDED_DIR / f"{session_id}.json"


OPEN_PULL_REQUEST_NUMBERS = """query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor } nodes { ... on PullRequest { number } } } }"""


def open_pull_request_numbers(slug: str) -> list[int]:
    """The numbers of the repo's open pull requests from the signed-in GitHub account."""
    numbers, cursor = [], None
    while True:
        command = ["gh", "api", "graphql", "-f", f"query={OPEN_PULL_REQUEST_NUMBERS}",
                   "-f", f"q=repo:{slug} is:pr is:open author:@me"]
        page = json.loads(run(command + (["-f", f"cursor={cursor}"] if cursor else [])))["data"]["search"]
        numbers += [node["number"] for node in page["nodes"] if node]
        if not page["pageInfo"]["hasNextPage"]:
            return numbers
        cursor = page["pageInfo"]["endCursor"]

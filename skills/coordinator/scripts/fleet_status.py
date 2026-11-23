#!/usr/bin/env python3
"""
Print where one coordinator's work stands, grouped by who acts next.

  fleet_status.py [<domain>]        default: the coordinator in this terminal

The rows come from two sources, so work stays listed after its delegate's
session ends: the delegates `hc ls` lists, and the open pull
requests the domain's backlog names anywhere, as #<number> or pull/<number>.

With the inbox mod installed, it also lists the questions and tasks each
inbox holds, and the open findings each delegate's inbox holds. A
delegate's findings reach the user only when you record them in your own
inbox.

fleet.py says which sessions are a domain's delegates. A delegate ended with
delegate_end.py stays listed, as ended.

A delegate is joined to a pull request by its worktree's branch, or by its
commit when the worktree has no branch checked out. Pull requests are those
opened from the signed-in GitHub account. The repos are those the
coordinator and its delegates work in, those the backlog links a pull
request in, and the Git checkouts that its Boundary section names by path.

Who an open pull request waits on, first match wins:

  the fleet    draft, failing checks, a merge conflict, an unresolved review
               thread, or changes requested with no commit since
  CI           checks still running
  another pull request
               its base branch is another open pull request's branch
  a reviewer   a review is required and none approves yet, or a person asked
               for changes and a commit followed
  the user     a person approved, a bot approved while people are still
               requested, or the repo requires no review

Each wait shows the time of the event that started it. Where GitHub's
answer holds no such event, the row says when the pull request opened.

Exit status: 0 printed, 2 a source could not be read. It never prints a
partial table.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet  # noqa: E402
import inbox_state  # noqa: E402
from fleet import CheckError, age, parse_time, repo_of  # noqa: E402
from pr_waits import waits_on  # noqa: E402
from resume import resume_text  # noqa: E402
from status_prs import fetch_pull_requests  # noqa: E402

DEFAULT_BRANCHES = ("main", "master")


def collect(args: argparse.Namespace) -> dict:
    now = parse_time(args.at) if args.at else dt.datetime.now(dt.timezone.utc)
    herd = fleet.Fleet(args.terminals)
    agents, backlogs = herd.agents, herd.backlogs
    domain = args.domain or herd.own_domain()
    coordinator = herd.coordinator_of(domain)
    members = herd.members_of(domain)
    live_sessions = {fleet.session_of(agent) for agent in agents}
    ended = [r for r in fleet.ended_records(domain) if r["session"] not in live_sessions]

    checkout = herd.checkouts
    worked_in = herd.repos_worked_in(domain) | {
        slug for slug in (repo_of(record["cwd"], checkout)[0] for record in ended) if slug
    }
    slugs = worked_in | herd.repos_of(domain)

    saved_prs = dict(item.split("=", 1) for item in args.prs)
    pull_requests: dict[str, list[dict]] = {}
    for slug in sorted(slugs):
        name = slug.split("/")[-1]
        if name in saved_prs:
            pull_requests[slug] = json.loads(Path(saved_prs[name]).read_text())["prs"]
        else:
            pull_requests[slug] = fetch_pull_requests(slug, now)

    saved_inbox = inbox_state.saved_inbox(Path(args.inbox_store) if args.inbox_store else inbox_state.inbox_store())

    def asks_of(agent: dict, holder: str) -> list[dict]:
        session = saved_inbox.get(fleet.session_of(agent))
        status_line = agent["inbox_status"]
        asks = [
            dict(holder=holder, kind=item["kind"], ask=item["ask"], count=1,
                 since=dt.datetime.fromtimestamp(item["at"] / 1000, dt.timezone.utc))
            for item in (session["ledger"]["items"] if session else [])
        ]
        if not session and agent["inbox_asks"]:
            asks.append(dict(holder=holder, kind="ask", ask=status_line, count=agent["inbox_asks"], since=None))
        if agent["status"] == "blocked":
            asks.insert(0, dict(holder=holder, kind="dialog", ask=status_line or "read its terminal", count=1, since=None))
        elif status_line.startswith("! "):
            asks.insert(0, dict(holder=holder, kind="stopped", ask=status_line, count=1, since=None))
        return asks

    def findings_of(agent: dict, holder: str) -> list[dict]:
        return [
            dict(holder=holder, title=finding.get("title") or "",
                 since=dt.datetime.fromtimestamp(finding["at"] / 1000, dt.timezone.utc) if finding.get("at") else None)
            for finding in inbox_state.open_findings(saved_inbox.get(fleet.session_of(agent)))
        ]

    asks = asks_of(coordinator, "coordinator") if coordinator else []
    findings: list[dict] = []
    def pull_request_at(cwd: str) -> tuple[str | None, str | None, str | None, dict | None]:
        slug, branch, commit, _ = repo_of(cwd, checkout)
        candidates = [
            pr for pr in pull_requests.get(slug, [])
            if (branch and branch not in DEFAULT_BRANCHES and pr["headRefName"] == branch)
            or (commit and pr.get("headRefOid") == commit)
        ]
        candidates.sort(key=lambda pr: (pr["state"] == "OPEN", pr["createdAt"]))
        return slug, branch, commit, candidates[-1] if candidates else None

    delegates = []
    claimed: set[tuple[str, int]] = set()

    def add_delegate(agent: dict, task: str, status: str, session_id: str, **extra) -> None:
        cwd = agent["cwd"]
        slug, branch, commit, pr = pull_request_at(cwd)
        if pr:
            claimed.add((slug, pr["number"]))
        delegates.append(dict(
            task=task, name=agent.get("name"), address=agent["address"], session=session_id[:8], status=status,
            last_response=None if status == "working" else fleet.last_response(session_id, now),
            cwd=cwd, repo=slug, branch=branch, commit=commit, pr=pr, problem=repo_of(cwd, checkout)[3], **extra,
        ))

    for member in members:
        agent, task = member["agent"], member["task"]
        add_delegate(
            agent, task, agent["status"] or "unknown", fleet.session_of(agent),
            placed_by="its fleet.domain label" if member["by_label"] else "backlog names its session",
            in_backlog=member["by_backlog"],
        )
        asks += asks_of(agent, f"[{task}]")
        findings += findings_of(agent, f"[{task}]")
    for record in ended:
        stand_in = dict(name=record["name"], address=None, cwd=record["cwd"], session=record["session"], status=None,
                        inbox_asks=0, inbox_status="")
        add_delegate(stand_in, record["task"], "ended", record["session"], placed_by="ended record",
                     in_backlog=True, ended_at=parse_time(record["ended_at"]))
        asks += asks_of(stand_in, f"[{record['task']}], ended")
        findings += findings_of(stand_in, f"[{record['task']}], ended")

    member_addresses = {member["agent"]["address"] for member in members}
    held_elsewhere: dict[tuple[str, int], str] = {}
    for agent in agents:
        label = agent["tab_label"]
        if agent["address"] in member_addresses or not fleet.label_parts(label) or herd.coordinated_domain(agent):
            continue
        slug, _, _, pr = pull_request_at(agent["cwd"])
        if pr:
            held_elsewhere[(slug, pr["number"])] = label

    backlog_only, unnamed = [], []
    every_backlog = {name: path.read_text() for name, path in backlogs.items()}
    open_numbers = [pr["number"] for prs in pull_requests.values() for pr in prs if pr["state"] == "OPEN"]
    for slug, prs in pull_requests.items():
        for pr in prs:
            if pr["state"] != "OPEN":
                continue
            needs_link = open_numbers.count(pr["number"]) > 1
            named_by = [
                name for name, text in every_backlog.items()
                if fleet.names_pull_request(text, slug, pr["number"], needs_link)
            ]
            if (slug, pr["number"]) in claimed:
                continue
            if domain in named_by:
                backlog_only.append(dict(repo=slug, pr=pr, held_by=held_elsewhere.get((slug, pr["number"])),
                                         also_named_by=[name for name in named_by if name != domain]))
            elif not named_by and slug in worked_in:
                unnamed.append(dict(repo=slug, pr=pr))

    open_branches = {
        slug: {pr["headRefName"]: pr["number"] for pr in prs if pr["state"] == "OPEN"}
        for slug, prs in pull_requests.items()
    }
    return dict(now=now, domain=domain, coordinator=coordinator, delegates=delegates, asks=asks, findings=findings,
                backlog_only=backlog_only, unnamed=unnamed, repos=sorted(pull_requests), open_branches=open_branches)


def delegate_note(delegate: dict, now: dt.datetime) -> str:
    if delegate["status"] == "ended":
        return f"[{delegate['task']}] ended {age(delegate['ended_at'], now)} ago"
    if delegate["status"] == "working":
        return f"[{delegate['task']}] working"
    return f"[{delegate['task']}] {delegate['status']}, quiet {age(delegate['last_response'], now)}"


def render(state: dict) -> str:
    now = state["now"]
    delegates = state["delegates"]
    by_number: dict[tuple[str, int], tuple[dict, list[str]]] = {}
    open_branches = state["open_branches"]
    for delegate in delegates:
        if delegate["pr"] and delegate["pr"]["state"] == "OPEN":
            key = (delegate["repo"], delegate["pr"]["number"])
            by_number.setdefault(key, (delegate["pr"], []))[1].append(delegate_note(delegate, now))
    for item in state["backlog_only"]:
        holder = f"held by another domain's delegate: {item['held_by']}" if item["held_by"] else "no live delegate"
        if item["also_named_by"] and not item["held_by"]:
            holder += f", also in the {' and '.join(item['also_named_by'])} backlog"
        by_number[(item["repo"], item["pr"]["number"])] = (item["pr"], [holder])
    rows = [(repo, pr, holders) for (repo, _), (pr, holders) in by_number.items()]
    without_pr = [d for d in delegates if not (d["pr"] and d["pr"]["state"] == "OPEN")]

    groups: dict[str, list[str]] = {"the user": [], "a reviewer": [], "the fleet": [], "CI": [], "another pull request": []}
    asks = state["asks"]
    start_of_time = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    waiting_prs = [
        (ask["since"] or start_of_time, "the user",
         f"{age(ask['since'], now) if ask['since'] else 'now':>4}  {ask['kind']:<8}  {ask['holder']}  {ask['ask']}")
        for ask in asks
    ]
    for repo, pr, holders in rows:
        who, what, event, since = waits_on(pr, open_branches[repo])
        link = f"[#{pr['number']}]({pr['url']})" if pr.get("url") else f"#{pr['number']}"
        waiting_prs.append((since or now, who, f"{age(since, now):>4}  {link} {what}, {event} {age(since, now)} ago  ·  {'; '.join(holders)}  ·  {pr['title'][:70]}"))
    for _, who, line in sorted(waiting_prs, key=lambda row: row[0]):
        groups[who].append(line)

    counts = {}
    for delegate in delegates:
        counts[delegate["status"]] = counts.get(delegate["status"], 0) + 1
    count_text = ", ".join(f"{n} {status}" for status, n in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"
    local = now.astimezone()
    def counted(n: int, one: str, many: str) -> str:
        return f"{n} {one if n == 1 else many}"

    out = [
        f"{state['domain']} status, {local:%m-%d %H:%M} {local.tzname()}",
        f"{counted(len(delegates), 'delegate', 'delegates')} ({count_text}). "
        f"{counted(len(rows), 'open pull request', 'open pull requests')}. "
        f"{counted(sum(a['count'] for a in asks), 'question or task waits', 'questions and tasks wait')} on the user."
        + (f" {counted(len(state['findings']), 'finding sits', 'findings sit')} in delegates' inboxes."
           if state["findings"] else ""),
    ]
    if not state["coordinator"]:
        out.append(f"No terminal is labelled as the coordinator for {state['domain']}.")

    headings = (("the user", "Waits on the user"), ("a reviewer", "Waits on a reviewer"),
                ("the fleet", "Waits on the fleet"), ("CI", "Waits on CI"),
                ("another pull request", "Waits on another pull request"))
    for key, heading in headings:
        if groups[key]:
            out += ["", f"{heading} ({len(groups[key])})"] + [f"  {line}" for line in groups[key]]

    if state["findings"]:
        out += ["", f"Findings in delegates' inboxes ({len(state['findings'])})"]
        for finding in sorted(state["findings"], key=lambda f: f["since"] or start_of_time):
            out.append(f"  {age(finding['since'], now) if finding['since'] else 'now':>4}  {finding['holder']}  {finding['title']}")

    if without_pr:
        out += ["", f"Delegates with no open pull request ({len(without_pr)})"]
        for delegate in sorted(without_pr, key=lambda d: d["last_response"] or now):
            pr = delegate["pr"]
            if pr:
                ended = parse_time(pr["mergedAt"] or pr["closedAt"])
                note = f"#{pr['number']} {'merged' if pr['mergedAt'] else 'closed'} {age(ended, now)} ago"
            elif delegate["problem"]:
                note = delegate["problem"]
            elif delegate["branch"] in DEFAULT_BRANCHES:
                note = f"on {delegate['branch']}, no pull request"
            else:
                note = f"no pull request for {delegate['branch']}"
            out.append(f"  {delegate_note(delegate, now)}  ·  {note}")

    missing = [d for d in delegates if not d["in_backlog"]]
    if missing:
        out += ["", f"Delegates the backlog's Open section does not name ({len(missing)}): "
                + ", ".join(f"[{d['task']}] {d['session']}" for d in missing)]
    if state["unnamed"]:
        by_repo: dict[str, list[int]] = {}
        for item in state["unnamed"]:
            by_repo.setdefault(item["repo"], []).append(item["pr"]["number"])
        for repo, numbers in by_repo.items():
            out += ["", f"Open pull requests in {repo} that no backlog names ({len(numbers)}): "
                    + " ".join(f"#{n}" for n in sorted(numbers))]
    return "\n".join(out)


def as_json(state: dict) -> str:
    def slim(pr: dict | None, repo: str | None) -> dict | None:
        if not pr:
            return None
        waits = waits_on(pr, state["open_branches"][repo]) if pr["state"] == "OPEN" else None
        return dict(number=pr["number"], state=pr["state"], branch=pr["headRefName"],
                    waits_on=waits[0] if waits else None, wait_state=waits[1] if waits else None,
                    wait_event=waits[2] if waits else None, wait_since=waits[3] if waits else None)

    return json.dumps(dict(
        at=state["now"], domain=state["domain"], repos=state["repos"],
        delegates=[dict(d, pr=slim(d["pr"], d["repo"])) for d in state["delegates"]],
        asks=state["asks"],
        findings=state["findings"],
        backlog_only=[dict(item, pr=slim(item["pr"], item["repo"])) for item in state["backlog_only"]],
        unnamed=[dict(repo=item["repo"], number=item["pr"]["number"]) for item in state["unnamed"]],
    ), indent=1, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain", nargs="?", help="such as web-app; default: the coordinator in this terminal")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    parser.add_argument("--terminals", metavar="FILE", help="read a saved JSON list of `hc get` output")
    parser.add_argument("--prs", metavar="REPO=FILE", action="append", default=[],
                        help='read saved pull requests for the repo of that name: {"prs": [...]}')
    parser.add_argument("--inbox-store", metavar="FILE", help="read a saved copy of the inbox mod's state")
    parser.add_argument("--resume", action="store_true",
                        help="first print the backlog sections a coordinator reads after compaction or a restart")
    parser.add_argument("--at", metavar="TIME", help="ISO time to measure ages from; default: now")
    args = parser.parse_args()

    try:
        if args.resume and args.json:
            raise CheckError("--resume prints text; run it without --json")
        state = collect(args)
        output = resume_text(state["domain"], render(state)) if args.resume else as_json(state) if args.json else render(state)
    except (CheckError, OSError, KeyError, TypeError, ValueError, IndexError) as error:
        print(f"fleet_status: could not check: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

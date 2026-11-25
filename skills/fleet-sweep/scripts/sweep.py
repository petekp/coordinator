#!/usr/bin/env python3
"""Inventory a coordinator's leftovers and remove the ones that are safe.

  sweep.py report <repo>... [--base BRANCH]... [--keep PATH]... [--no-sizes]
  sweep.py remove <worktree> [--base BRANCH]... [--discard-untracked] [--park]
  sweep.py prune-branches <repo> (--list | <branch>...) [--base BRANCH]...
  sweep.py prune-ended [--list]

report prints one verdict per item: SAFE (remove it now), CLOSE (the task
looks finished but a session still runs there), ASK (finished-looking work
that would be lost), KEEP (in use). It changes nothing, except that the
config key fast_forward_main_checkouts makes it fast-forward each main
checkout to its remote's default branch and print a MAIN row for each one
it moved or left alone. It also counts the
records of ended delegates whose PR merged or closed or whose worktree is
gone.

prune-ended removes those records from the coordinator's <state_dir>/ended
and prints each one's name, domain, program and session id. --list prints
them and removes nothing.

remove re-checks one worktree, moves its ignored files other than build output
to <main checkout>/.scratch/parked/<worktree name>, removes the worktree and
deletes its branch, and refuses when anything would be lost. With --park it
keeps the branch, or creates parked/<worktree name> for a detached worktree.
Each parked folder holds a .parked-at file with the time it was parked, for a
clean-up job that deletes old ones; nothing here deletes them. prune-branches
lists local branches whose work is on a base branch or whose PR merged, or
deletes the named ones and prints each one's hash.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "coordinator", "scripts"))
from config import CONFIG  # noqa: E402
from ended import ended_record_rows, prune_ended  # noqa: E402
from diskuse import scratch_rows, simulator_rows, size_mb  # noqa: E402
from mainsync import fast_forward_row  # noqa: E402
from parking import park_ignored, split_build_output, unpark  # noqa: E402
from prs import branch_prs, is_ancestor, merged_heads, pr_summary, pull_requests, run  # noqa: E402

APP_WORKTREE_ROOTS = (
    os.path.expanduser("~/.codex/worktrees/"),
    "/.claude/worktrees/",
)
PROTECTED_BRANCHES = {"main", "master", "next", "develop"}
SELF_EXITING = {"caffeinate"}


def git(repo, *args):
    return run("git", "-C", repo, *args)


def default_bases(repo, extra):
    head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    local_default = next((b for b in ("main", "master") if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{b}")), None)
    bases = [head.strip()] if head else [local_default] if local_default else []
    return bases + [b for b in extra if git(repo, "rev-parse", "--verify", "-q", b)]


def worktrees(repo):
    out = git(repo, "worktree", "list", "--porcelain") or ""
    for block in out.strip().split("\n\n"):
        fields = dict(
            line.split(" ", 1) if " " in line else (line, "")
            for line in block.splitlines()
        )
        yield fields.get("worktree", ""), fields.get("branch", "").removeprefix("refs/heads/")


HC_NOT_FOUND = 3
AGENT_PROGRAMS = {"claude", "codex"}


def runs_an_agent(listed, terminal):
    """Claude Code, or a program that reports a status, as coding agents do and shells do not. An agent
    started from its terminal's shell has no status in `hc ls`, and `hc get` shows it in the foreground."""
    argv = (terminal.get("foreground") or {}).get("argv") or []
    started_from_its_shell = bool(argv) and os.path.basename(argv[0]) in AGENT_PROGRAMS
    return listed.get("agent") == "claude-code" or listed.get("status") is not None or started_from_its_shell


def live_sessions():
    """Agent sessions in live herdr-core terminals, each with the folder it runs in now.

    None when herdr-core could not be read, apart from a terminal that ended
    between the listing and its lookup.
    """
    out = run("hc", "ls", "--json")
    if out is None:
        return None
    sessions = []
    for listed in json.loads(out).get("terminals", []):
        try:
            got = subprocess.run(["hc", "get", listed["address"], "--json"], capture_output=True, text=True)
        except OSError:
            return None
        if got.returncode == HC_NOT_FOUND:
            continue
        if got.returncode != 0:
            return None
        terminal = json.loads(got.stdout)
        if not runs_an_agent(listed, terminal):
            continue
        labels = terminal.get("labels") or {}
        cwd = (terminal.get("foreground") or {}).get("cwd") or terminal.get("cwd")
        sessions.append({
            "name": terminal.get("name") or terminal["address"],
            "tab": labels.get("where") or "no tab",
            "cwd": os.path.realpath(cwd) if cwd else "",
            "inbox": labels.get("inbox.status") if labels.get("inbox.needs-you") else None,
        })
    return sessions


def processes_by_cwd():
    """(pid, command, cwd or executable path) for every process."""
    out = run("lsof", "-d", "cwd,txt", "-Fpcn")
    if out is None:
        return None
    rows, pid, cmd = [], None, None
    for line in out.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid = value
        elif tag == "c":
            cmd = value
        elif tag == "n":
            rows.append((pid, cmd, value))
    return rows


def inside(path, folder):
    """lsof gives real paths, and live_sessions resolves each cwd, so folder must be a real path."""
    return (path + "/").startswith(folder.rstrip("/") + "/")


def processes_inside(procs, folder):
    return sorted({
        f"{pid} {cmd}" for pid, cmd, name in procs
        if inside(name, folder) and cmd not in SELF_EXITING
    })


def unsaved_commits(worktree, bases, landed=()):
    out = git(worktree, "log", "--oneline", "HEAD", "--not", "--remotes", *bases, *landed)
    return None if out is None else len(out.splitlines())


def dirty_files(worktree):
    out = git(worktree, "status", "--porcelain")
    return None if out is None else out.splitlines()


def never_committed(repo, branch):
    """Such a branch is an ancestor of its base, like a merged one. A missing
    reflog leaves it unknown, and it then counts as committed."""
    log = git(repo, "reflog", "show", "--format=%gs", f"refs/heads/{branch}")
    if not log:
        return False
    return not any(entry.startswith(("commit", "cherry-pick", "revert")) for entry in log.splitlines())


def branch_landed(repo, branch, bases, pr_state):
    if pr_state == "MERGED":
        return True
    return any(is_ancestor(repo, branch, base) for base in bases)


def worktree_rows(repo, bases, prs, sessions, procs, sizes, keep):
    main_checkout = os.path.realpath(repo)
    listed = list(worktrees(repo))
    candidates = [
        (path, branch) for path, branch in listed
        if os.path.realpath(path) != main_checkout
        and not any(r in path for r in APP_WORKTREE_ROOTS)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        yield from pool.map(
            lambda wt: worktree_row(repo, *wt, bases, prs, sessions, procs, sizes, keep),
            candidates,
        )
    landed = list(landed_branches(repo, bases, prs, {b for _, b in listed}))
    if landed:
        yield "SAFE", f"{len(landed)} landed branches in {repo} · list them: sweep.py prune-branches {repo} --list"


def worktree_row(repo, path, branch, bases, prs, sessions, procs, sizes, keep):
    pr_state, pr_label = pr_summary(branch_prs(repo, branch, prs))
    dirty = dirty_files(path)
    unsaved = unsaved_commits(path, bases, merged_heads(path, branch, prs))
    git_failed = dirty is None or unsaved is None
    real = os.path.realpath(path)
    here = [s for s in sessions if inside(s["cwd"], real)]
    pids = processes_inside(procs, real)
    on_base = branch in PROTECTED_BRANCHES or any(b.split("/")[-1] == branch for b in bases)
    not_started = bool(branch) and not pr_state and never_committed(repo, branch)
    landed = bool(branch) and not not_started and branch_landed(repo, branch, bases, pr_state)
    finished = not on_base and (landed or pr_state == "CLOSED")

    if on_base or any(inside(real, k) for k in keep):
        verdict = "KEEP"
    elif not branch or git_failed:
        verdict = "KEEP" if here else "ASK"
    elif here and (dirty or unsaved):
        verdict = "KEEP"
    elif dirty or unsaved:
        verdict = "ASK"
    elif here:
        verdict = "CLOSE" if finished else "KEEP"
    elif pids:
        verdict = "ASK"
    elif finished:
        verdict = "SAFE"
    else:
        verdict = "KEEP"

    notes = [pr_label or ("detached" if not branch else "base branch" if on_base else "no PR")]
    if landed and not pr_state:
        notes.append("merged into base")
    if git_failed:
        notes.append("git status or git log failed here")
    if dirty:
        notes.append(f"{len(dirty)} uncommitted")
    if unsaved:
        notes.append(f"{unsaved} commits on no remote or base")
    for s in here:
        notes.append(f"session {s['name']} in \"{s['tab']}\"" + (f" inbox: {s['inbox']}" if s["inbox"] else ""))
    if pids and not here:
        notes.append("processes: " + "; ".join(pids[:4]))
    size = f"{size_mb(path):>6}M " if sizes and verdict != "KEEP" else ""
    return verdict, f"{size}worktree {path} [{branch or 'detached'}] " + " · ".join(notes)


def landed_branches(repo, bases, prs, checked_out):
    """(branch, short sha, pr state) for local branches whose work is on a base."""
    out = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname:short)", "refs/heads") or ""
    base_names = {b.split("/")[-1] for b in bases} | PROTECTED_BRANCHES
    for line in out.splitlines():
        branch, sha = line.split()
        if branch in checked_out or branch in base_names:
            continue
        pr_state, _ = pr_summary(branch_prs(repo, branch, prs))
        if pr_state != "OPEN" and branch_landed(repo, branch, bases, pr_state):
            yield branch, sha, pr_state


def checked_out_branches(repo):
    return {branch for _, branch in worktrees(repo)}


def orphan_process_rows(procs, worktree_roots):
    seen = set()
    for pid, cmd, path in procs:
        if pid in seen or cmd in SELF_EXITING:
            continue
        under_root = any(inside(path, r) for r in worktree_roots)
        if under_root and not os.path.exists(path):
            seen.add(pid)
            yield "SAFE", f"process {pid} {cmd} · runs in deleted folder {path}"


def report(args):
    sessions = live_sessions()
    procs = processes_by_cwd()
    if sessions is None or procs is None:
        return refuse("hc ls or lsof failed, so no verdict is safe; run the report again")
    rows = []
    roots = set()
    for repo in args.repos:
        repo = os.path.abspath(os.path.expanduser(repo))
        bases = default_bases(repo, args.base)
        prs = pull_requests(repo) or {}
        keep = [os.path.realpath(os.path.expanduser(k)) for k in args.keep]
        if CONFIG["fast_forward_main_checkouts"]:
            moved = fast_forward_row(repo, bases)
            if moved:
                rows.append(moved)
        rows += worktree_rows(repo, bases, prs, sessions, procs, not args.no_sizes, keep)
        rows += scratch_rows(repo)
        roots.add(os.path.realpath(os.path.join(os.path.dirname(repo), "worktrees")))
        roots.add(os.path.realpath(os.path.join(repo, ".scratch")))
    rows += orphan_process_rows(procs, roots)
    rows += ended_record_rows()
    if args.simulators:
        rows += simulator_rows()
    order = {"MAIN": 0, "SAFE": 1, "CLOSE": 2, "ASK": 3, "KEEP": 4}
    for verdict, text in sorted(rows, key=lambda r: order[r[0]]):
        print(f"{verdict:5} {text}")
    if not rows:
        print("nothing to sweep: no worktrees, landed branches, stale scratch or stray processes")
    return 0


def refuse(message):
    print(f"refused: {message}", file=sys.stderr)
    return 1


def ignored_paths(worktree):
    """(paths to park, build output to drop), relative to the worktree, or None
    when git cannot list them. `git worktree remove` deletes ignored files."""
    out = git(worktree, "ls-files", "-z", "--others", "--ignored", "--exclude-standard",
              "--directory", "--no-empty-directory")
    if out is None:
        return None
    park, drop = [], []
    for rel in out.split("\0"):
        if rel:
            split_build_output(worktree, rel.rstrip("/"), park, drop)
    return park, drop


def listed(paths, limit=8):
    return ", ".join(paths[:limit]) + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def parking_branch(repo, worktree):
    name = f"parked/{os.path.basename(worktree)}"
    candidate, n = name, 2
    while git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{candidate}"):
        candidate, n = f"{name}-{n}", n + 1
    return candidate


def remove(args):
    path = os.path.realpath(os.path.expanduser(args.worktree))
    repo = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not repo:
        return refuse(f"{path} is not a git worktree")
    repo = os.path.dirname(repo.strip())
    if os.path.realpath(path) == os.path.realpath(repo):
        return refuse(f"{path} is the main checkout")
    branch = (git(path, "symbolic-ref", "--short", "-q", "HEAD") or "").strip()
    bases = default_bases(repo, args.base)
    prs = (pull_requests(repo) or {}) if (branch and not args.park) else {}

    dirty = dirty_files(path)
    unsaved = unsaved_commits(path, bases, merged_heads(path, branch, prs))
    sessions = live_sessions()
    procs = processes_by_cwd()
    if dirty is None or unsaved is None:
        return refuse("git status or git log failed in this worktree")
    if sessions is None or procs is None:
        return refuse("hc ls or lsof failed; try again")
    untracked_only = dirty and all(line.startswith("??") for line in dirty)
    discard = args.discard_untracked and not branch and untracked_only
    if dirty and not discard:
        return refuse(f"{len(dirty)} uncommitted files:\n" + "\n".join(dirty[:20]))
    if unsaved and not args.park:
        return refuse("commits on no remote or base branch; pass --park to keep the branch")
    if any(inside(s["cwd"], path) for s in sessions):
        return refuse("a Claude Code session still runs here; end its terminal first")
    pids = processes_inside(procs, path)
    if pids:
        return refuse("processes still run here: " + "; ".join(pids))
    ignored = ignored_paths(path)
    if ignored is None:
        return refuse("git ls-files failed, so the ignored files to keep are unknown")
    ignored, build_output = ignored

    created_branch = None
    if args.park and not branch:
        created_branch = parking_branch(repo, path)
        head = (git(path, "rev-parse", "HEAD") or "").strip()
        if not head or git(repo, "branch", created_branch, head) is None:
            return refuse(f"could not create branch {created_branch} for the detached worktree")
        branch = created_branch
    try:
        parked, moves = park_ignored(path, repo, ignored) if ignored else (None, [])
    except OSError as error:
        if created_branch:
            git(repo, "branch", "-D", created_branch)
        return refuse(f"could not park ignored files: {error}")
    cmd = ["git", "-C", repo, "worktree", "remove", path]
    if discard:
        cmd.insert(5, "--force")
    if subprocess.run(cmd).returncode != 0:
        if parked:
            unpark(parked, moves)
        if created_branch:
            git(repo, "branch", "-D", created_branch)
        return refuse("git worktree remove failed")
    print(f"removed worktree {path}")
    if parked:
        print(f"moved ignored files to {parked}, {size_mb(parked)} MB: {listed(ignored)}")
    if build_output:
        print(f"dropped build output: {listed(build_output)}")

    if created_branch:
        sha = (git(repo, "rev-parse", "--short", created_branch) or "").strip()
        print(f"created branch {created_branch} {sha} for the parked detached worktree")
    elif args.park:
        print(f"kept branch {branch} for the parked task")
    elif branch and branch not in PROTECTED_BRANCHES:
        sha = (git(repo, "rev-parse", "--short", branch) or "").strip()
        pr_state, _ = pr_summary(branch_prs(repo, branch, prs))
        flag = "-D" if pr_state == "MERGED" else "-d"
        if subprocess.run(["git", "-C", repo, "branch", flag, branch]).returncode == 0:
            print(f"deleted branch {branch} {sha}")
        else:
            print(f"kept branch {branch} {sha}: not merged into a base")
    return 0


def prune_branches(args):
    repo = os.path.abspath(os.path.expanduser(args.repo))
    bases = default_bases(repo, args.base)
    prs = pull_requests(repo) or {}
    for branch, sha, pr_state in landed_branches(repo, bases, prs, checked_out_branches(repo)):
        if args.list:
            print(f"{branch} {sha} {'PR merged' if pr_state == 'MERGED' else 'merged into base'}")
            continue
        if branch not in args.branches:
            continue
        flag = "-D" if pr_state == "MERGED" else "-d"
        if subprocess.run(["git", "-C", repo, "branch", flag, branch], capture_output=True).returncode == 0:
            print(f"deleted branch {branch} {sha}")
        else:
            print(f"kept branch {branch} {sha}: git refused")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("repos", nargs="+")
    rep.add_argument("--base", action="append", default=[])
    rep.add_argument("--keep", action="append", default=[])
    rep.add_argument("--no-sizes", action="store_true")
    rep.add_argument("--simulators", action="store_true", help="also list iOS simulators over 500 MB")
    rem = sub.add_parser("remove")
    rem.add_argument("worktree")
    rem.add_argument("--base", action="append", default=[])
    rem.add_argument("--discard-untracked", action="store_true")
    rem.add_argument("--park", action="store_true")
    pru = sub.add_parser("prune-branches")
    pru.add_argument("repo")
    pru.add_argument("branches", nargs="*")
    pru.add_argument("--list", action="store_true")
    pru.add_argument("--base", action="append", default=[])
    sub.add_parser("prune-ended").add_argument("--list", action="store_true")
    args = parser.parse_args()
    commands = {"report": report, "remove": remove, "prune-branches": prune_branches, "prune-ended": prune_ended}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

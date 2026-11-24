"""
The pull requests opened from the signed-in GitHub account, by branch, and
which of them speak for a branch. sweep.py and ended.py share these.
"""
import json
import subprocess
import sys


def run(*args, cwd=None):
    try:
        out = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def pull_requests(repo, slug=None):
    """PRs by branch, from the checkout at repo or from the GitHub repo slug.

    Returns None when gh fails so callers can tell a failed lookup from a repo
    with zero pull requests ({}).
    """
    where = ["--repo", slug] if slug else []
    out = run(
        "gh", "pr", "list", *where, "--author", "@me", "--state", "all", "--limit", "500",
        "--json", "headRefName,headRefOid,state,number,mergedAt", cwd=repo,
    )
    if out is None:
        if slug:
            print(f"warning: gh pr list failed for {slug}, so its ended-delegate records whose PR merged stay", file=sys.stderr)
        else:
            print(f"warning: gh pr list failed in {repo}, so worktrees whose PR merged show KEEP", file=sys.stderr)
        return None
    prs = {}
    for pr in json.loads(out):
        prs.setdefault(pr["headRefName"], []).append(pr)
    return prs


def pr_summary(prs):
    if not prs:
        return None, ""
    states = {p["state"] for p in prs}
    label = ",".join(f"#{p['number']} {p['state'].lower()}" for p in prs)
    if "OPEN" in states:
        return "OPEN", label
    return ("MERGED" if "MERGED" in states else "CLOSED"), label


def is_ancestor(repo, commit, descendant):
    return subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", commit, descendant],
        capture_output=True,
    ).returncode == 0


def branch_prs(repo, branch, prs):
    """A closed or merged PR speaks for the branch only when the branch holds
    nothing past that PR's head, so a reused branch name does not inherit it."""
    return [
        p for p in prs.get(branch, [])
        if p["state"] == "OPEN" or (p.get("headRefOid") and is_ancestor(repo, branch, p["headRefOid"]))
    ]


def merged_heads(worktree, branch, prs):
    """[HEAD] when worktree's HEAD equals the headRefOid of a merged PR for branch."""
    if not branch or not prs:
        return []
    head = (run("git", "-C", worktree, "rev-parse", "HEAD") or "").strip()
    if not head:
        return []
    return [head] if any(p["state"] == "MERGED" and p.get("headRefOid") == head for p in prs.get(branch, [])) else []

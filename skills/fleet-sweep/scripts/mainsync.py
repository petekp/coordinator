"""Fast-forward a main checkout to its remote's default branch, for sweep.py report."""
from prs import run


def git(repo, *args):
    return run("git", "-C", repo, *args)


def fast_forward_row(repo, bases):
    """A MAIN row when the main checkout moved or could not move, else None.

    Moves only by `git merge --ff-only`, so it never touches tracked changes,
    local commits or untracked files.
    """
    base = bases[0] if bases else None
    if not base or "/" not in base:
        return "MAIN", f"{repo} not updated: no remote default branch"
    remote, default = base.split("/", 1)
    if git(repo, "fetch", "--quiet", remote) is None:
        return "MAIN", f"{repo} not updated: git fetch {remote} failed"
    current = (git(repo, "symbolic-ref", "--short", "-q", "HEAD") or "").strip()
    if current != default:
        return "MAIN", f"{repo} not updated: on {current or 'a detached HEAD'}, not {default}"
    tracked_changes = git(repo, "status", "--porcelain", "--untracked-files=no")
    if tracked_changes is None:
        return "MAIN", f"{repo} not updated: git status failed"
    if tracked_changes.strip():
        return "MAIN", f"{repo} not updated: {len(tracked_changes.splitlines())} tracked files changed"
    counts = git(repo, "rev-list", "--left-right", "--count", f"HEAD...{base}")
    if counts is None:
        return "MAIN", f"{repo} not updated: could not compare {default} with {base}"
    ahead, behind = (int(n) for n in counts.split())
    if ahead and behind:
        return "MAIN", f"{repo} not updated: {default} has {ahead} local commits and is {behind} behind {base}"
    if ahead or not behind:
        return None
    before = (git(repo, "rev-parse", "--short", "HEAD") or "").strip()
    if git(repo, "merge", "--ff-only", "--quiet", base) is None:
        return "MAIN", f"{repo} not updated: git merge --ff-only {base} failed"
    after = (git(repo, "rev-parse", "--short", "HEAD") or "").strip()
    return "MAIN", f"{repo} fast-forwarded {default} {before} -> {after}, {behind} commits"

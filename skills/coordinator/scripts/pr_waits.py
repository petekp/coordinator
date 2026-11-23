"""Who an open pull request waits on, as fleet_status.py groups its rows."""

from __future__ import annotations

import datetime as dt

from fleet import parse_time


def waits_on(pr: dict, open_branches: dict[str, int]) -> tuple[str, str, str, dt.datetime | None]:
    """Who an open pull request waits on, the state, and the event that started the wait.

    open_branches maps each open pull request's branch to its number.
    """
    last_commit = ((pr.get("commits") or {}).get("nodes") or [{}])[0].get("commit", {})
    checks = (last_commit.get("statusCheckRollup") or {}).get("state")
    committed = last_commit.get("committedDate") or ""
    opened = ("opened", parse_time(pr["createdAt"]))
    last_commit_at = ("last commit", parse_time(committed)) if committed else opened

    open_threads = [t for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]]
    thread_starts = [
        c["createdAt"] for t in open_threads for c in ((t.get("comments") or {}).get("nodes") or [])
    ]
    reviews = pr["latestOpinionatedReviews"]["nodes"]
    asked_for_changes = [r for r in reviews if r["state"] == "CHANGES_REQUESTED"]
    person_approvals = [
        r["submittedAt"] for r in reviews
        if r["state"] == "APPROVED" and (r["author"] or {}).get("__typename") == "User"
    ]
    approvals = person_approvals or [r["submittedAt"] for r in reviews if r["state"] == "APPROVED"]
    ready_events = ((pr.get("timelineItems") or {}).get("nodes") or [])
    ready = ("marked ready", parse_time(ready_events[-1]["createdAt"])) if ready_events and ready_events[-1] else opened
    approved = ("approved", parse_time(max(approvals))) if approvals else opened

    if pr["isDraft"]:
        return ("the fleet", "draft", *opened)
    if checks in ("FAILURE", "ERROR"):
        return ("the fleet", "failing checks", *last_commit_at)
    if pr["mergeable"] == "CONFLICTING":
        return ("the fleet", "merge conflict", *opened)
    if open_threads:
        started = ("thread opened", parse_time(min(thread_starts))) if thread_starts else opened
        return ("the fleet", f"{len(open_threads)} open review thread{'s' if len(open_threads) > 1 else ''}", *started)
    if asked_for_changes:
        if all(committed > r["submittedAt"] for r in asked_for_changes):
            return ("a reviewer", "second look after fixes", *last_commit_at)
        return ("the fleet", "changes requested", "asked", parse_time(max(r["submittedAt"] for r in asked_for_changes)))
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        return ("the fleet", "changes requested", *opened)
    if checks in ("PENDING", "EXPECTED"):
        return ("CI", "checks running", *last_commit_at)
    if pr.get("baseRefName") in open_branches:
        return ("another pull request", f"stacked on #{open_branches[pr['baseRefName']]}", *opened)
    if pr["reviewDecision"] == "REVIEW_REQUIRED":
        if person_approvals:
            return ("a reviewer", "one more required review", *approved)
        return ("a reviewer", "first review", *ready)
    if pr["reviewDecision"] == "APPROVED" and not person_approvals and pr["reviewRequests"]["totalCount"]:
        return ("the user", "approved by a bot only, people still requested", *approved)
    return ("the user", "ready to merge", *approved)

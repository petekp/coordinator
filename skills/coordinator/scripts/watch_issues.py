"""
How the fleet watcher finds new issues: the open issues created in each
repo the domain owns, read with one GraphQL query per repo, newest first.
The domain owns the checkouts its backlog's Boundary names before the line
that starts "Adjacent", bold or not.

Each repo's record holds when it was last read and the numbers already
seen, so a restarted watcher reports none again. A repo read for the first
time records the time and reports nothing. An issue is left for a later
poll until it is ISSUE_GRACE_SECONDS old, so the session that filed it has
time to record it. An issue is reported once, unless the backlog or an open
item in the coordinator's inbox names it.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import fleet
import inbox_state
from watch_prs import run_json

ADJACENT_LINE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?Adjacent\b", re.MULTILINE)
ISSUE_GRACE_SECONDS = 300
OVERLAP_SECONDS = 1200
PAGES_PER_POLL = 5
ISSUES_QUERY = """query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $cursor, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor } nodes { number title url createdAt } } } }"""


class IssueReadFailed(Exception):
    pass


def epoch(stamp):
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def open_issues_since(slug, cutoff):
    """The repo's open issues created at or after cutoff, and why GitHub did not give them. Either is None."""
    owner, name = slug.split("/", 1)
    found, cursor = [], None
    for _ in range(PAGES_PER_POLL):
        command = ["gh", "api", "graphql", "-f", f"query={ISSUES_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        data, failure = run_json(*command, *(["-f", f"cursor={cursor}"] if cursor else []))
        repository = ((data or {}).get("data") or {}).get("repository") if isinstance(data, dict) else None
        if not repository:
            return None, failure or "GitHub's answer held no repository"
        page = repository["issues"]
        for node in page["nodes"]:
            if not node:
                continue
            if epoch(node["createdAt"]) < cutoff:
                return found, None
            found.append(node)
        if not page["pageInfo"]["hasNextPage"]:
            return found, None
        cursor = page["pageInfo"]["endCursor"]
    return found, None


def names_issue(text, slug, number, needs_link):
    """A link to the issue, owner/name#<number> in any case, or a bare #<number> when the domain watches one repo."""
    if re.search(rf"{re.escape(slug)}(?:/issues/|#){number}(?!\d)", text, re.IGNORECASE):
        return True
    return not needs_link and bool(re.search(rf"(?<![\w/])#{number}(?!\d)", text))


def issue_lines(slugs, seen, now, fetch, backlog_text, inbox_text):
    """The issue lines for this poll, each repo's new record, and why each failed repo's read failed.

    fetch(slug, cutoff) returns the open issues created at or after cutoff, and a failure.
    """
    lines, records, failed = [], {}, {}
    needs_link = len(slugs) > 1
    for slug in sorted(slugs):
        record = seen.get(slug)
        if not isinstance(record, dict) or not isinstance(record.get("read_at"), (int, float)):
            records[slug] = {"read_at": now, "numbers": {}}
            continue
        cutoff = record["read_at"] - OVERLAP_SECONDS
        try:
            issues, failure = fetch(slug, cutoff)
            if issues is None:
                raise IssueReadFailed(failure)
            numbers = {key: created for key, created in (record.get("numbers") or {}).items() if created >= cutoff}
            new_lines = []
            for issue in sorted(issues, key=lambda node: node["number"]):
                key, created = str(issue["number"]), epoch(issue["createdAt"])
                if key in numbers or now - created < ISSUE_GRACE_SECONDS:
                    continue
                numbers[key] = created
                if not any(names_issue(text, slug, issue["number"], needs_link) for text in (backlog_text, inbox_text)):
                    new_lines.append(f"issue {issue['number']} · {issue['title']} · {issue['url']}")
        except IssueReadFailed as error:
            failed[slug], records[slug] = str(error), record
            continue
        except Exception as error:
            failed[slug], records[slug] = f"issue read failed: {type(error).__name__}: {error}"[:300], record
            continue
        lines += new_lines
        records[slug] = {"read_at": now, "numbers": numbers}
    return lines, records, failed


def owned_repos(text, cache):
    """Repos of the checkouts the backlog's Boundary names before its line that starts "Adjacent"."""
    boundary = fleet.section_text(text, "## Boundary")
    adjacent = ADJACENT_LINE.search(boundary)
    owned = boundary[:adjacent.start()] if adjacent else boundary
    return fleet.boundary_repos(f"## Boundary\n{owned}", cache)


def domain_issue_sources(domain, agents, store=None):
    """The repos the domain owns, the backlog's text, and the text of the open items in the coordinator's
    saved inbox."""
    herd = fleet.Fleet(agents=agents)
    text = herd.text(domain)
    coordinator = herd.coordinator_of(domain)
    inbox_text = ""
    if coordinator:
        try:
            saved = inbox_state.saved_inbox(store or inbox_state.inbox_store()).get(fleet.session_of(coordinator)) or {}
        except (fleet.CheckError, OSError, ValueError):
            saved = {}
        items = ((saved.get("ledger") or {}).get("items")) or []
        inbox_text = json.dumps(items, ensure_ascii=False)
    return owned_repos(text, herd.checkouts), text, inbox_text

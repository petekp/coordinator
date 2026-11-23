"""
How the fleet watcher finds new issues: the open issues created in each
repo the domain's backlog names in its Boundary, read with one GraphQL query
per repo, newest first.

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

ISSUE_GRACE_SECONDS = 300
OVERLAP_SECONDS = 1200
PAGES_PER_POLL = 5
ISSUES_QUERY = """query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $cursor, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor } nodes { number title url createdAt } } } }"""


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
    """A link to the issue, owner/name#<number>, or a bare #<number> when the domain watches one repo."""
    if re.search(rf"{re.escape(slug)}(?:/issues/|#){number}(?!\d)", text):
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
        issues, failure = fetch(slug, record["read_at"] - OVERLAP_SECONDS)
        if issues is None:
            failed[slug] = failure
            records[slug] = record
            continue
        numbers = {key: created for key, created in (record.get("numbers") or {}).items()
                   if created >= now - OVERLAP_SECONDS}
        for issue in sorted(issues, key=lambda node: node["number"]):
            key, created = str(issue["number"]), epoch(issue["createdAt"])
            if key in numbers or now - created < ISSUE_GRACE_SECONDS:
                continue
            numbers[key] = created
            if any(names_issue(text, slug, issue["number"], needs_link) for text in (backlog_text, inbox_text)):
                continue
            lines.append(f"issue {issue['number']} · {issue['title']} · {issue['url']}")
        records[slug] = {"read_at": now, "numbers": numbers}
    return lines, records, failed


def domain_issue_sources(domain, agents, store=None):
    """The repos the domain's Boundary names, the backlog's text, and the text of the open items in the
    coordinator's saved inbox."""
    herd = fleet.Fleet(agents=agents)
    text = herd.backlog(domain).read_text()
    coordinator = herd.coordinator_of(domain)
    inbox_text = ""
    if coordinator:
        try:
            saved = inbox_state.saved_inbox(store or inbox_state.inbox_store()).get(fleet.session_of(coordinator)) or {}
        except (fleet.CheckError, OSError, ValueError):
            saved = {}
        items = ((saved.get("ledger") or {}).get("items")) or []
        inbox_text = json.dumps(items, ensure_ascii=False)
    return fleet.boundary_repos(text, herd.checkouts), text, inbox_text

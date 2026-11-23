"""
What a fleet watcher armed with --domain watches, found again on each poll:
the domain's live delegates, as fleet.Fleet.members_of finds them, and the
open pull requests from the signed-in GitHub account that the domain's
backlog names, as fleet_status.py finds them.

A delegate is watched under its terminal name, or its address when it has
none, and a pull request as owner/name#number. --agent and --pr can name
the same ones another way, by address or checkout path. The snapshots an
earlier watcher saved under the other name carry over, so switching between
the forms neither loses nor repeats an event.
"""
import backlog
import fleet
from watch_cache import cached_open_pr_numbers
from watch_prs import repo_name


def named(agents):
    """Each agent under its terminal name and its terminal address."""
    return {key: a for a in agents for key in (a.get("name"), a["address"]) if key}


def watch_name(agent):
    return agent.get("name") or agent["address"]


def domain_member_names(agents, domain):
    """The domain's live delegates' names, and why its backlog could not be read. The names are None when
    it could not."""
    try:
        return {watch_name(member["agent"]) for member in fleet.Fleet(agents=agents).members_of(domain)}, None
    except Exception as error:
        return None, str(error)[:200]


def watched_agent_names(members, watched_before, agents_seen, listed):
    """The domain's delegates now, and each delegate watched before that no live terminal runs any more,
    so its `gone` prints once. One that still runs but left the domain is dropped without a line."""
    vanished = {name for name in watched_before - members
                if name not in listed and agents_seen.get(name, {}).get("present")}
    return members | vanished


def domain_pull_requests(domain, agents):
    """The open pull requests the domain's backlog names, as owner/name#number; each searched repo's open
    numbers; why a repo's search failed; and the backlog's text."""
    herd = fleet.Fleet(agents=agents)
    text = herd.text(domain)
    ended_repos = {fleet.repo_of(record["cwd"], herd.checkouts)[0] for record in fleet.ended_records(domain)}
    open_numbers, failed = {}, {}
    for slug in sorted((herd.repos_of(domain) | ended_repos) - {None}):
        try:
            open_numbers[slug] = cached_open_pr_numbers(slug)
        except Exception as error:
            failed[slug] = f"open pull request search failed: {str(error)[:200]}"
    found = {f"{slug}#{number}" for slug, number in backlog.pull_requests_named(text, open_numbers)}
    return found, open_numbers, failed, text


def watched_pr_specs(found, watched_before, open_numbers, failed, prs_seen, text):
    """The pull requests the backlog names, and each one watched before that it no longer names but that
    may still need a line:
    - its repo's search failed;
    - it left the open state and no poll has seen that yet;
    - its repo is no longer searched, the backlog still names it, and no poll has seen it leave the open
      state.
    One still open that the backlog no longer names is dropped without a line."""
    kept = set()
    for spec in watched_before - found:
        slug, number = spec.rsplit("#", 1)
        state = prs_seen.get(spec, {}).get("state")
        left_open_state = slug in open_numbers and int(number) not in open_numbers[slug]
        repo_not_searched = slug not in open_numbers and slug not in failed
        still_named = fleet.names_pull_request(text, slug, int(number), needs_link=False)
        if (slug in failed or (left_open_state and state == "OPEN")
                or (repo_not_searched and still_named and state in (None, "OPEN"))):
            kept.add(spec)
    return found | kept


def slug_spec(spec, prs_seen):
    """The pull request as owner/name#number, or None when its checkout's repo cannot be named."""
    repo, number = spec.rsplit("#", 1)
    slug = prs_seen.get(spec, {}).get("slug") or repo_name(repo)[0]
    return f"{slug}#{number}" if slug else None


def written_before_domain(saved, state_source):
    """The state file holds snapshots but not the list of what its watcher watched."""
    return (saved.get("source") == state_source and "watched_agents" not in saved
            and bool(saved.get("agents") or saved.get("prs")))


def under_watch_names(names, agents, agents_seen):
    """The names, each live one as --domain names its terminal, starting from the snapshot an earlier
    watcher saved under the terminal's address or other name."""
    by_name = named(agents)
    renamed = set()
    for name in names:
        agent = by_name.get(name)
        canonical = watch_name(agent) if agent else name
        if canonical != name and canonical not in agents_seen and name in agents_seen:
            agents_seen[canonical] = dict(agents_seen[name])
        renamed.add(canonical)
    return renamed


def given_terminals(given, agents):
    """The --domain names of the live terminals that --agent already names another way."""
    by_name = named(agents)
    return {watch_name(by_name[name]) for name in given if name in by_name} - set(given)


def same_pr_keys(prs_seen, spec, slug):
    """The keys an earlier watcher saved this pull request under in the other form: its owner/name for a
    checkout path, and every checkout path for its owner/name. Two checkout paths keep separate
    snapshots, as --pr always has."""
    number = spec.rsplit("#", 1)[1]
    slug_key = f"{slug}#{number}"
    if spec != slug_key:
        return [slug_key] if slug_key in prs_seen else []
    return [key for key, entry in prs_seen.items()
            if key != spec and key.rsplit("#", 1)[1] == number and entry.get("slug") == slug]


def adopt_earlier_pr(prs_seen, spec):
    """Start a pull request's snapshot from the one saved under its other key. Of several checkout paths,
    the one that has seen it leave the open state or has counted the most comments is the latest."""
    if spec in prs_seen:
        return
    named_as = slug_spec(spec, prs_seen)
    earlier = named_as and same_pr_keys(prs_seen, spec, named_as.rsplit("#", 1)[0])
    if earlier:
        latest = max(earlier, key=lambda key: (prs_seen[key].get("state") not in (None, "OPEN"),
                                               prs_seen[key].get("comments") or 0))
        prs_seen[spec] = dict(prs_seen[latest])


def copy_to_same_pr(prs_seen, now_prs):
    """Write each polled pull request's snapshot under its other saved keys too."""
    for spec, entry in now_prs.items():
        if entry.get("slug"):
            for key in same_pr_keys(prs_seen, spec, entry["slug"]):
                prs_seen[key] = dict(entry)


def copy_to_same_terminal(agents_seen, now_agents, agents):
    """Write each polled delegate's snapshot under its terminal's other name, when an earlier watcher
    saved it that way."""
    by_name = named(agents)
    for name, entry in now_agents.items():
        agent = by_name.get(name)
        if agent:
            for key in {agent.get("name"), agent["address"]} - {None, name}:
                if key in agents_seen:
                    agents_seen[key] = dict(entry)

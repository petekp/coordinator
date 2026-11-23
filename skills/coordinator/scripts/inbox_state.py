"""
What the inbox mod saved for each session: its open questions and tasks,
and its open findings. Claude Code keeps the mod's state as
inbox_<source>-<hash>.json; config.py's inbox_store names the file when
more than one exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from config import CONFIG, CONFIG_FILE
from fleet import HOME, CheckError, session_of

INBOX_STORES = HOME / ".claude" / "plugins" / "store"


def inbox_store() -> Path | None:
    """The inbox mod's saved state, which Claude Code keeps as inbox_<source>-<hash>.json. None without the inbox."""
    if CONFIG["inbox_store"]:
        return Path(CONFIG["inbox_store"]).expanduser()
    found = sorted(INBOX_STORES.glob("inbox_*.json"))
    if len(found) > 1:
        raise CheckError(f"more than one inbox store: {', '.join(map(str, found))}. "
                         f"Name the one to read as inbox_store in {CONFIG_FILE}")
    return found[0] if found else None


def saved_inbox(store: Path | None) -> dict:
    """Each session's saved inbox, by session id."""
    if store is None or not store.is_file():
        return {}
    return {key[2:]: value for key, value in json.loads(store.read_text()).items() if key.startswith("s:")}


def open_findings(saved_session: dict | None) -> list[dict]:
    """The findings a session's saved inbox holds open."""
    return list(((saved_session or {}).get("ledger") or {}).get("findings") or [])


def inbox_items(agent: dict, store: Path | None = None) -> list[str]:
    """What the agent's inbox holds: its saved questions, tasks and open findings, else its terminal's count of asks."""
    saved = saved_inbox(store or inbox_store()).get(session_of(agent))
    if saved:
        return ([f"{item['kind']}: {item.get('ask') or item.get('title')}" for item in saved["ledger"]["items"]]
                + [f"finding: {finding.get('title')}" for finding in open_findings(saved)])
    if agent["inbox_asks"]:
        return [f"not saved by the inbox; its terminal shows {agent['inbox_asks']} asks: {agent['inbox_status']}"]
    return []

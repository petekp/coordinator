"""Whether a delegate's terminal is running Claude Code that has not reported a status yet."""
import fleet
from fleet import CheckError


def process_alive(address: str) -> bool:
    """The terminal is live and its process has not exited, as `hc get` shows it."""
    try:
        terminal = fleet.terminal_or_none(address)
    except CheckError:
        return False
    return bool(terminal) and terminal.get("state") == "live" and terminal.get("exit_code") is None


def note(address: str) -> str:
    return (f"Claude Code is running in terminal {address} but has not reported a status within "
            f"{fleet.SECONDS_TO_WAIT_FOR_AGENT} seconds. Its startup hooks may still be running.")

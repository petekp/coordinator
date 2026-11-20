"""
The label a fleet watcher puts on its terminal to say it only waits.

Run by Claude Code as a background command, the watcher labels the
terminal it runs in with fleet.waiting.<task ID>=fleet_watch.py, where the
task ID is the one Claude Code gave the command, and removes the label when
it exits. Claude Code reports a session that sits at its prompt with a
background command as working. The label tells the Herdr agents list that
this command only waits, so the list shows the session as idle.
"""
from __future__ import annotations

import atexit
import fcntl
import os
import re
import subprocess
import sys

WAITING_LABEL_PREFIX = "fleet.waiting."
LABEL_TIMEOUT_SECONDS = 10


def own_task_id():
    """The ID Claude Code gave this background command. It writes the command's output to <session>/tasks/<ID>.output."""
    try:
        if hasattr(fcntl, "F_GETPATH"):
            path = fcntl.fcntl(sys.stdout.fileno(), fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode()
        else:
            path = os.readlink(f"/proc/self/fd/{sys.stdout.fileno()}")
    except (AttributeError, OSError, ValueError):
        return None
    match = re.fullmatch(r".*/tasks/(\w+)\.output", path)
    return match.group(1) if match else None


def set_label(terminal, assignment):
    try:
        subprocess.run(["hc", "label", terminal, assignment], capture_output=True, timeout=LABEL_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def declare_waiting():
    """Label this terminal with the watcher's task, and return the function that removes the label.

    The label's key holds the task ID, so each waiting command writes only its own label. A label
    left by a watcher killed with SIGKILL names a task that no longer runs, and the list ignores it.
    """
    terminal, task = os.environ.get("HC_TERMINAL_ID"), own_task_id()
    if not terminal or not task:
        return lambda: None
    key = WAITING_LABEL_PREFIX + task
    removed = []

    def stop_waiting():
        if not removed:
            removed.append(True)
            set_label(terminal, f"{key}=")
    atexit.register(stop_waiting)
    set_label(terminal, f"{key}=fleet_watch.py")
    return stop_waiting

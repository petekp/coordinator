"""The fleet watcher reports a REPORT.md written by a delegate whose terminal reports no status."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import add_script_paths

add_script_paths()
import fleet_watch  # noqa: E402
import watch_agents  # noqa: E402


class TestReportWithoutStatus(unittest.TestCase):
    def test_report_written_by_a_delegate_with_no_status_is_reported(self):
        agent = {"address": "t2", "name": "d1", "status": None, "turn": 0, "cwd": "", "session": "s-d1",
                 "inbox_asks": 0, "inbox_status": ""}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".scratch").mkdir()
            report = Path(folder) / ".scratch" / "REPORT.md"
            report.write_text("- status: done\n")
            agent = dict(agent, cwd=folder)
            clock = fleet_watch.MacClock(None)
            no_inbox = lambda: {}
            before = watch_agents.agent_snapshot(["d1"], [agent], {}, clock, saved_inbox=no_inbox)
            os.utime(report, (before["d1"]["report_mtime"] + 60,) * 2)
            after = watch_agents.agent_snapshot(["d1"], [agent], before, clock, saved_inbox=no_inbox)
        self.assertEqual(list(watch_agents.agent_events(before, after)), [f"report d1 {report}"])


if __name__ == "__main__":
    unittest.main()

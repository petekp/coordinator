"""Tests for fleet_watch.py, watch_agents.py, watch_issues.py, due_steps.py, watch_prs.py, and watcher_check.py."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import COORD_SCRIPTS, Sandbox, add_script_paths

add_script_paths()
import due_steps  # noqa: E402
import fleet  # noqa: E402
import fleet_watch  # noqa: E402
import watch_agents  # noqa: E402
import watch_issues  # noqa: E402
import watch_prs  # noqa: E402


class TestDueSteps(unittest.TestCase):
    def test_parses_due_dates_skips_checked_and_fences(self):
        text = (
            "- Active task due: 2026-11-18 check metrics\n"
            "- [x] Done task due: 2026-11-15 ignored\n"
            "- ~~Struck due: 2026-11-16~~\n"
            "```\n- Fenced due: 2026-11-14\n```\n"
            "- Parent task\n  - Child step due: 2026-11-19 follow up\n"
        )
        steps = due_steps.dated_steps(text)
        dates = [d for d, _, _ in steps]
        self.assertEqual(dates, ["2026-11-18", "2026-11-19"])
        self.assertIn("Parent task ›", steps[1][2])


class TestWatcherEvents(unittest.TestCase):
    def test_agent_events_ignores_idle_compaction(self):
        old = {
            "d1": {"present": True, "status": "done", "turns": 5, "asks": 0, "report": None,
                   "report_mtime": None, "stuck": False, "responded": "2026-11-19T01:00:00Z", "compacted": None}
        }
        new = {
            "d1": {"present": True, "status": "done", "turns": 6, "asks": 0, "report": None,
                   "report_mtime": None, "stuck": False, "responded": "2026-11-19T01:00:00Z",
                   "compacted": "2026-11-19T02:00:00Z"}
        }
        self.assertEqual(list(watch_agents.agent_events(old, new)), [])

    def test_agent_events_fires_settled_ask_report_and_gone(self):
        old = {
            "d1": {"present": True, "status": "working", "turns": 1, "asks": 0, "report": "/a/REPORT.md",
                   "report_mtime": 100.0, "stuck": False, "responded": "t1", "compacted": None},
            "d2": {"present": True, "status": "done", "turns": 1, "asks": 0, "report": None,
                   "report_mtime": None, "stuck": False, "responded": "t1", "compacted": None},
        }
        new = {
            "d1": {"present": True, "status": "done", "turns": 2, "asks": 1, "inbox": "1 · Q?",
                   "report": "/a/REPORT.md", "report_mtime": 200.0, "stuck": False, "responded": "t2", "compacted": None},
            "d2": {"present": False, "ended": False, "session": "s2"},
        }
        evs = list(watch_agents.agent_events(old, new))
        self.assertIn("settled d1 done", evs)
        self.assertIn("ask d1 1 · Q?", evs)
        self.assertIn("report d1 /a/REPORT.md", evs)
        self.assertIn("gone d2", evs)

    def test_settled_and_report_lines_carry_report_counts_and_inbox_findings(self):
        old = {"d1": {"present": True, "status": "working", "turns": 1, "asks": 0, "report": None,
                      "report_mtime": None, "stuck": False, "responded": "t1", "compacted": None}}
        new = {"d1": {"present": True, "status": "done", "turns": 2, "asks": 0, "report": "/a/REPORT.md",
                      "report_mtime": 200.0, "report_counts": "2 questions, 1 issue, 0 findings", "findings": 3,
                      "stuck": False, "responded": "t2", "compacted": None}}
        evs = list(watch_agents.agent_events(old, new))
        self.assertIn("settled d1 done · 3 findings in its inbox", evs)
        self.assertIn("report d1 /a/REPORT.md · 2 questions, 1 issue, 0 findings · 3 findings in its inbox", evs)

    def test_report_counts_entries_under_fixed_headings(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "REPORT.md"
            report.write_text(
                "- status: done\n- what changed: x\n\n"
                "## Questions for the user\n\n- Ship it?\n  - nested detail\n1. Rename it?\n\n"
                "### Issues filed:\n\n- None\n- None filed so far.\n- Nothing.\n\n"
                "## Findings\n\n- Clock skew\n```\n- not an entry\n```\n\n## Notes\n\n- not counted\n"
            )
            self.assertEqual(watch_agents.report_counts(str(report)), "2 questions, 0 issues, 1 finding")
            report.write_text("- status: done\n- each open question: none\n")
            self.assertIsNone(watch_agents.report_counts(str(report)))

    def test_snapshot_reads_findings_and_report_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".scratch").mkdir()
            (Path(folder) / ".scratch" / "REPORT.md").write_text("## Findings\n\n- one\n- two\n")
            agent = {"address": "t2", "name": "d1", "status": "done", "turn": 3, "cwd": folder, "session": "s-d1",
                     "inbox_asks": 0, "inbox_status": ""}
            saved = {"s-d1": {"ledger": {"items": [], "findings": [{"title": "a"}, {"title": "b"}]}}}
            snap = watch_agents.agent_snapshot(["d1"], [agent], {}, fleet_watch.MacClock(None),
                                              saved_inbox=lambda: saved)
        self.assertEqual(snap["d1"]["findings"], 2)
        self.assertEqual(snap["d1"]["report_counts"], "2 findings")

    def test_urgent_reads_only_the_status_of_a_settled_line(self):
        self.assertTrue(fleet_watch.URGENT.search("settled d1 blocked · 2 findings in its inbox"))
        self.assertTrue(fleet_watch.URGENT.search("settled d1 error"))
        self.assertFalse(fleet_watch.URGENT.search("settled d1 done · 1 finding in its inbox"))
        self.assertFalse(fleet_watch.URGENT.search("issue 7 · Save fails with an error · https://x/7"))

    def test_pr_events_detects_merge_checks_and_comments(self):
        old = {"org/repo#10": {"state": "OPEN", "checks": "pending", "review": "", "comments": 1}}
        new = {"org/repo#10": {"state": "MERGED", "checks": "success", "review": "APPROVED", "comments": 3}}
        evs = list(watch_prs.pr_events(old, new))
        self.assertIn("pr repo#10 merged", evs)
        self.assertIn("pr repo#10 checks success", evs)
        self.assertIn("pr repo#10 review approved", evs)
        self.assertIn("pr repo#10 comments 2", evs)

    def test_mac_clock_detects_sleep(self):
        an_hour_ago = time.time() - 3600
        clk = fleet_watch.MacClock([an_hour_ago, time.monotonic() - 10, 0.0])
        self.assertGreater(clk.mac_woke_at, an_hour_ago + 3000)
        self.assertLess(clk.awake_seconds_since(an_hour_ago), 60)


def issue(number, title, created):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created))
    return {"number": number, "title": title, "createdAt": stamp,
            "url": f"https://github.com/org/app/issues/{number}"}


def checkout(root, name, url):
    folder = root / name
    folder.mkdir()
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    subprocess.run(["git", "-C", str(folder), "remote", "add", "origin", url], check=True)
    return folder


class TestOwnedRepos(unittest.TestCase):
    def test_owned_repos_stop_at_the_adjacent_line_and_boundary_repos_do_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(os.path.realpath(folder))
            owned = checkout(root, "app", "https://github.com/org/app.git")
            adjacent = checkout(root, "lib", "git@github.com:org/lib.git")
            for adjacent_line in ("Adjacent: ", "- **Adjacent (read freely):** "):
                text = (f"# demo\n\n## Boundary\n\n- **Owns:** {owned}\n{adjacent_line}{adjacent}\n\n"
                        f"## Open\n\n- see {adjacent}\n")
                self.assertEqual(watch_issues.owned_repos(text, {}), {"org/app"})
                self.assertEqual(fleet.boundary_repos(text, {}), {"org/app", "org/lib"})
            text = f"## Boundary\n\nOwns {owned} and {adjacent}.\n"
            self.assertEqual(watch_issues.owned_repos(text, {}), {"org/app", "org/lib"})


class TestIssueLines(unittest.TestCase):
    def test_first_read_records_and_later_reads_report_each_unnamed_issue_once(self):
        now = 1_800_000_000.0
        first, seen, failed = watch_issues.issue_lines({"org/app"}, {}, now, lambda slug, cutoff: ([], None), "", "")
        self.assertEqual((first, failed), ([], {}))

        issues = [issue(11, "Crash on save", now - 400), issue(12, "In backlog", now - 400),
                  issue(13, "In inbox", now - 400), issue(14, "Too new", now - 30)]
        asked = []

        def fetch(slug, cutoff):
            asked.append(cutoff)
            return [i for i in issues if watch_issues.epoch(i["createdAt"]) >= cutoff], None

        backlog = "- Fix: Org/App#12 is being fixed by d1.\n"
        inbox = '[{"ask": "Fix #13 now or later?", "context": "https://github.com/org/app/issues/13"}]'
        lines, seen, failed = watch_issues.issue_lines({"org/app"}, seen, now + 90, fetch, backlog, inbox)
        self.assertEqual(lines, ["issue 11 · Crash on save · https://github.com/org/app/issues/11"])
        self.assertEqual(asked, [now - watch_issues.OVERLAP_SECONDS])

        lines, seen, _ = watch_issues.issue_lines({"org/app"}, seen, now + 400, fetch, backlog, inbox)
        self.assertEqual(lines, ["issue 14 · Too new · https://github.com/org/app/issues/14"])
        lines, seen, _ = watch_issues.issue_lines({"org/app"}, seen, now + 490, fetch, backlog, inbox)
        self.assertEqual(lines, [])
        lines, seen, _ = watch_issues.issue_lines({"org/app"}, seen, now + 1000, fetch, backlog, inbox)
        self.assertEqual(lines, [])
        lines, seen, _ = watch_issues.issue_lines({"org/app"}, seen, now + 2500, fetch, backlog, inbox)
        self.assertEqual(lines, [])

    def test_bare_number_names_an_issue_only_when_one_repo_is_watched(self):
        now = 1_800_000_000.0
        seen = {"org/app": {"read_at": now - 90, "numbers": {}}, "org/api": {"read_at": now - 90, "numbers": {}}}

        def fetch(slug, cutoff):
            return ([issue(5, f"Bug in {slug}", now - 600)] if slug == "org/app" else []), None

        lines, _, _ = watch_issues.issue_lines(set(seen), seen, now, fetch, "- see #5\n", "")
        self.assertEqual(lines, ["issue 5 · Bug in org/app · https://github.com/org/app/issues/5"])
        lines, _, _ = watch_issues.issue_lines({"org/app"}, seen, now, fetch, "- see #5\n", "")
        self.assertEqual(lines, [])

    def test_failed_read_keeps_the_record_and_reports_why(self):
        record = {"org/app": {"read_at": 100.0, "numbers": {"3": 50.0}}}
        lines, seen, failed = watch_issues.issue_lines({"org/app"}, record, 200.0,
                                                       lambda slug, cutoff: (None, "gh exit 1: boom"), "", "")
        self.assertEqual((lines, seen, failed), ([], record, {"org/app": "gh exit 1: boom"}))

        def unexpected_answer(slug, cutoff):
            return [{"number": 4, "title": "t", "url": "u", "createdAt": None}], None

        lines, seen, failed = watch_issues.issue_lines({"org/app"}, record, 200.0, unexpected_answer, "", "")
        self.assertEqual((lines, seen), ([], record))
        self.assertIn("issue read failed", failed["org/app"])


class TestRuleLinesAndDueDates(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.write_stub("hc", "echo '{\"terminals\": []}'\n")

    def tearDown(self):
        self.sb.cleanup()

    def rule_lines(self) -> list[str]:
        res = self.sb.run_python("import fleet_watch; print('\\n'.join(fleet_watch.rule_lines('demo')))")
        self.assertEqual(res.returncode, 0, res.stderr)
        return [line for line in res.stdout.splitlines() if line]

    def test_done_over_its_limit_names_the_cut_and_notes_over_theirs_name_the_largest_items(self):
        done = "".join(f"- 2026-11-0{i % 9 + 1} Item {i} " + "x" * 200 + "\n" for i in range(120))
        self.sb.write_backlog("demo", f"# demo\n\n## Boundary\nOwns.\n\n## Open\n- One.\n\n## Done\n{done}")
        lines = self.rule_lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^rule · in your backlog, Done is \d+\.\d KB, limit 20 · Run \S+/backlog_check.py demo --cut-done")
        (self.sb.backlog_dir / "demo-backlog.md").unlink()
        self.sb.write_notes("demo", "# demo\n\n## Open\n- **Big.** " + "y" * 21_000 + "\n- Small.\n", "## Later\n\n## Done\n")
        lines = self.rule_lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r'^rule · in your notes, the notes file is 21\.\d KB, limit 20 · Its largest Open items: "Big\. y+…" 21\.\d KB; "Small\." ')

    def test_later_items_that_resume_hides_get_one_rule_line_a_week_with_the_count_resume_gives(self):
        later = "".join(f"- **Parked {i:02d}** " + "w" * 140 + "\n" for i in range(30))
        self.sb.write_notes("demo", "# demo\n\n## Open\n", f"## Later\n\n{later}\n## Done\n")
        res = self.sb.run_python(
            "import json, fleet_watch, resume\n"
            "seen = {}\n"
            "first = fleet_watch.rule_lines('demo', seen)\n"
            "again = fleet_watch.rule_lines('demo', seen)\n"
            "seen['demo'] -= 7 * 24 * 3600\n"
            "later = fleet_watch.rule_lines('demo', seen)\n"
            "print(json.dumps([first, again, later, resume.resume_text('demo', 'status')]))")
        self.assertEqual(res.returncode, 0, res.stderr)
        first, again, later, resumed = json.loads(res.stdout)
        hidden = re.search(r"The (\d+) oldest of its 30 items are not shown here", resumed)
        self.assertIsNotNone(hidden, resumed)
        self.assertEqual(len(first), 1)
        self.assertRegex(first[0], rf"^rule · in your notes, --resume hides the oldest {hidden.group(1)} of 30 Later items · ")
        self.assertEqual(again, [])
        self.assertEqual(len(later), 1)

    def test_no_later_rule_line_when_nothing_is_hidden_or_the_notes_are_in_one_piece(self):
        self.sb.write_notes("demo", "# demo\n\n## Open\n", "## Later\n\n- One.\n- Two.\n\n## Done\n")
        self.assertEqual(self.rule_lines(), [])
        later = "".join(f"- **Parked {i:02d}** " + "w" * 140 + "\n" for i in range(30))
        (self.sb.backlog_dir / "demo-notes.md").write_text("# demo\n\n## Open\n\n## Done\n")
        (self.sb.backlog_dir / "demo-later.md").write_text(f"## Later\n\n{later}\n")
        self.assertEqual(self.rule_lines(), [])

    def test_a_large_later_gets_no_size_rule(self):
        later = "".join(f"- **Parked {i:03d}** " + "w" * 400 + "\n" for i in range(120))
        self.sb.write_notes("demo", "# demo\n\n## Open\n", f"## Later\n\n{later}\n## Done\n")
        lines = self.rule_lines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("limit", lines[0])

    def test_a_separate_file_under_the_older_name_gets_a_rule_line(self):
        self.sb.write_notes("demo", "# demo\n\n## Open\n", "## Later\n\n## Done\n")
        self.sb.write_backlog("demo", "# demo\n\n## Open\n- Stray edit.\n\n## Done\n")
        lines = self.rule_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("demo-backlog.md is a separate file from", lines[0])

    def test_due_dates_in_later_fire_and_open_keys_do_not_change(self):
        self.sb.write_notes("demo", "# demo\n\n## Open\n- Check metrics due: 2026-11-18\n",
                            "## Later\n\n- Re-measure ranking due: 2026-11-20\n- Not yet due: 2026-12-30\n\n## Done\n- 11-15 x due: 2026-11-01\n")
        res = self.sb.run_python("import due_steps; lines, come = due_steps.due_lines('demo', '2026-11-22', []); print(lines); print(come)")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines()[0],
                         "['due 2026-11-18 · Check metrics due: 2026-11-18', 'due 2026-11-20 · Re-measure ranking due: 2026-11-20']")
        self.assertIn("'2026-11-18 Check metrics due: 2026-11-18'", res.stdout.splitlines()[1])


class TestWatcherCheckCLI(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n\n## Done\n")

    def tearDown(self):
        self.sb.cleanup()

    def test_reports_unwatched_coordinator_with_delegate(self):
        terms = [
            {"address": "t1", "name": None, "cwd": "/tmp", "labels": {"fleet.role": "coordinator", "fleet.domain": "demo"},
             "status": {"state": "done", "app": "claude-code"}, "foreground": {"pid": 1, "argv": ["claude"]}, "turn": 1},
            {"address": "t2", "name": "d1", "cwd": "/tmp", "labels": {"fleet.domain": "demo", "fleet.task": "work"},
             "status": {"state": "done", "app": "claude-code"}, "foreground": {"pid": 2, "argv": ["claude"]}, "turn": 1},
        ]
        tf = self.sb.root / "terms.json"
        tf.write_text(json.dumps(terms))
        res = self.sb.run_script(COORD_SCRIPTS / "watcher_check.py", "--terminals", str(tf))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("demo", res.stdout)
        self.assertIn("unwatched", res.stdout)


if __name__ == "__main__":
    unittest.main()

"""Tests for fleet-sweep scripts (ended.py, prs.py, parking.py, sweep.py)."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tests.helpers import SWEEP_SCRIPTS, Sandbox, add_script_paths

add_script_paths()
import parking  # noqa: E402
import prs as sweep_prs  # noqa: E402


class TestParking(unittest.TestCase):
    def test_build_output_patterns(self):
        self.assertTrue(parking.is_build_output("node_modules/pkg/index.js"))
        self.assertTrue(parking.is_build_output("out/bundle.js"))
        self.assertTrue(parking.is_build_output("debug.log"))
        self.assertFalse(parking.is_build_output(".scratch/BRIEF.md"))
        park, drop = [], []
        parking.split_build_output("/nonexistent", ".scratch/test.log", park, drop)
        self.assertEqual(park, [".scratch/test.log"])
        self.assertEqual(drop, [])


class TestPrsAndEnded(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_pr_summary(self):
        self.assertEqual(sweep_prs.pr_summary([]), (None, ""))
        self.assertEqual(
            sweep_prs.pr_summary([{"number": 5, "state": "MERGED"}]),
            ("MERGED", "#5 merged"),
        )
        self.assertEqual(
            sweep_prs.pr_summary([{"number": 5, "state": "CLOSED"}, {"number": 6, "state": "OPEN"}]),
            ("OPEN", "#5 closed,#6 open"),
        )

    def test_prune_ended_removes_merged_pr_record(self):
        ended_dir = self.sb.state_dir / "ended"
        ended_dir.mkdir(parents=True)
        rec_path = ended_dir / "sess-1.json"
        rec_path.write_text(json.dumps({
            "name": "task-a",
            "domain": "demo",
            "session": "sess-1",
            "cwd": str(self.sb.root / "gone-wt"),
            "repo": "org/repo",
            "branch": "feat-a",
            "ended_at": "2026-11-15T00:00:00+00:00",
        }))
        self.sb.write_stub("gh", 'echo \'[{"headRefName":"feat-a","headRefOid":"abc","state":"MERGED","number":1,"mergedAt":"2026-11-15T01:00:00Z"}]\'\n')
        res = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "prune-ended", "--list")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("task-a of demo", res.stdout)

        res2 = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "prune-ended")
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertFalse(rec_path.exists())

    def test_prune_ended_handles_zero_pr_repo_vs_gh_failure(self):
        ended_dir = self.sb.state_dir / "ended"
        ended_dir.mkdir(parents=True)
        rec_path = ended_dir / "sess-zero.json"
        rec_path.write_text(json.dumps({
            "name": "task-z",
            "domain": "demo",
            "session": "sess-zero",
            "cwd": str(self.sb.root / "gone-wt"),
            "repo": "org/no-prs-yet",
            "branch": "feat-z",
            "ended_at": "2026-11-15T00:00:00+00:00",
        }))
        # gh failure -> keep record (PR state unknown)
        self.sb.write_stub("gh", "exit 1\n")
        fail_res = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "prune-ended")
        self.assertEqual(fail_res.returncode, 0, fail_res.stderr)
        self.assertTrue(rec_path.exists())

        # gh succeeds with [] (0 PRs in repo) -> prune record
        self.sb.write_stub("gh", "echo '[]'\n")
        ok_res = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "prune-ended")
        self.assertEqual(ok_res.returncode, 0, ok_res.stderr)
        self.assertFalse(rec_path.exists())

    def test_squash_merged_worktree_without_remote_branch_is_safe_until_new_commit(self):
        repo = self.sb.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        (repo / ".gitignore").write_text(".scratch/\n")
        (repo / "README.md").write_text("base\n")
        subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

        wt_root = self.sb.root / "worktrees" / "repo"
        wt_root.mkdir(parents=True)
        wt_merged = wt_root / "squash-ok"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat-squash", str(wt_merged), "main"], check=True)
        (wt_merged / "a.txt").write_text("feature\n")
        subprocess.run(["git", "-C", str(wt_merged), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(wt_merged), "commit", "-q", "-m", "feat commit"], check=True)
        merged_oid = subprocess.run(["git", "-C", str(wt_merged), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        (wt_merged / ".scratch").mkdir()
        (wt_merged / ".scratch" / "BRIEF.md").write_text("brief\n")

        (repo / "a.txt").write_text("squashed on main\n")
        subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "squash merge (#9)"], check=True)

        wt_ahead = wt_root / "squash-ahead"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat-ahead", str(wt_ahead), merged_oid], check=True)
        (wt_ahead / "extra.txt").write_text("unmerged\n")
        subprocess.run(["git", "-C", str(wt_ahead), "add", "extra.txt"], check=True)
        subprocess.run(["git", "-C", str(wt_ahead), "commit", "-q", "-m", "post-merge local work"], check=True)

        wt_closed = wt_root / "closed-unmerged"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat-closed", str(wt_closed), merged_oid], check=True)

        prs_json = json.dumps([
            {"headRefName": "feat-squash", "headRefOid": merged_oid, "state": "MERGED", "number": 9, "mergedAt": "2026-11-23T01:00:00Z"},
            {"headRefName": "feat-ahead", "headRefOid": merged_oid, "state": "MERGED", "number": 10, "mergedAt": "2026-11-23T01:00:00Z"},
            {"headRefName": "feat-closed", "headRefOid": merged_oid, "state": "CLOSED", "number": 11, "mergedAt": None},
        ])
        self.sb.write_stub("gh", f"printf '%s\\n' '{prs_json}'\n")
        self.sb.write_stub("hc", 'echo \'{"terminals":[]}\'\n')
        self.sb.write_stub("lsof", "exit 0\n")

        rep = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "report", str(repo), "--no-sizes")
        self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
        lines = rep.stdout.splitlines()
        self.assertTrue(any(line.startswith("SAFE ") and "squash-ok [feat-squash] #9 merged" in line for line in lines), rep.stdout)
        self.assertTrue(any(line.startswith("ASK ") and "squash-ahead [feat-ahead]" in line and "commits on no remote or base" in line for line in lines), rep.stdout)
        self.assertTrue(any(line.startswith("ASK ") and "closed-unmerged [feat-closed] #11 closed" in line for line in lines), rep.stdout)

        refused_ahead = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "remove", str(wt_ahead))
        self.assertEqual(refused_ahead.returncode, 1)
        self.assertTrue(wt_ahead.exists())

        refused_closed = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "remove", str(wt_closed))
        self.assertEqual(refused_closed.returncode, 1)
        self.assertTrue(wt_closed.exists())

        removed_ok = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "remove", str(wt_merged))
        self.assertEqual(removed_ok.returncode, 0, removed_ok.stdout + removed_ok.stderr)
        self.assertFalse(wt_merged.exists())
        self.assertIn("deleted branch feat-squash", removed_ok.stdout)
        self.assertTrue((repo / ".scratch" / "parked" / "squash-ok" / ".scratch" / "BRIEF.md").is_file())

        self.sb.write_stub("gh", "exit 1\n")
        parked_ahead = self.sb.run_script(SWEEP_SCRIPTS / "sweep.py", "remove", "--park", str(wt_ahead))
        self.assertEqual(parked_ahead.returncode, 0, parked_ahead.stdout + parked_ahead.stderr)
        self.assertEqual(parked_ahead.stderr, "")
        self.assertFalse(wt_ahead.exists())


if __name__ == "__main__":
    unittest.main()

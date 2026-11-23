"""Tests for config.py and backlog_check.py."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tests.helpers import COORD_SCRIPTS, Sandbox, add_script_paths

add_script_paths()
import backlog  # noqa: E402
import config  # noqa: E402


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_missing_file_uses_defaults(self):
        loaded = config.load_config(self.sb.root / "missing.json")
        self.assertIsNone(loaded["notes_limit_kb"])
        self.assertIsNone(loaded["done_limit_kb"])
        self.assertEqual(backlog.limits(False, loaded), (40_000, 20_000))
        self.assertEqual(backlog.limits(True, loaded), (20_000, 100_000))
        self.assertEqual(backlog.limits(True, dict(loaded, notes_limit_kb=25)), (25_000, 100_000))

    def test_older_key_names_are_aliases(self):
        p = self.sb.root / "older.json"
        p.write_text(json.dumps({"backlog_dir": "~/notes", "backlog_limit_kb": 30}))
        loaded = config.load_config(p)
        self.assertEqual(loaded["notes_dir"], "~/notes")
        self.assertEqual(loaded["notes_limit_kb"], 30)
        self.assertNotIn("backlog_dir", loaded)
        p.write_text(json.dumps({"backlog_dir": "~/a", "notes_dir": "~/a"}))
        self.assertEqual(config.load_config(p)["notes_dir"], "~/a")
        p.write_text(json.dumps({"backlog_dir": "~/a", "notes_dir": "~/b"}))
        with self.assertRaises(ValueError):
            config.load_config(p)
        p.write_text(json.dumps({"notes_limit_kb": "20"}))
        with self.assertRaises(ValueError):
            config.load_config(p)

    def test_unknown_key_raises(self):
        p = self.sb.root / "bad.json"
        p.write_text(json.dumps({"not_a_key": 1}))
        with self.assertRaises(ValueError):
            config.load_config(p)

    def test_wrong_type_raises(self):
        p = self.sb.root / "badtype.json"
        p.write_text(json.dumps({"backlog_limit_kb": 40.5}))
        with self.assertRaises(ValueError):
            config.load_config(p)

    def test_delegate_env_default_validation_and_wrapping(self):
        loaded = config.load_config(self.sb.root / "missing.json")
        self.assertEqual(loaded["delegate_env"], {})
        self.assertEqual(
            config.with_delegate_env(["claude", "--model", "sonnet"], {"NODE_OPTIONS": "--max-old-space-size=4096"}),
            ["env", "NODE_OPTIONS=--max-old-space-size=4096", "claude", "--model", "sonnet"],
        )
        self.assertEqual(config.with_delegate_env(["claude"], {}), ["claude"])
        bad = self.sb.root / "bad_env.json"
        bad.write_text(json.dumps({"delegate_env": {"NODE_OPTIONS": 4096}}))
        with self.assertRaises(ValueError):
            config.load_config(bad)

    def test_deprecated_keys_accepted_and_ignored(self):
        p = self.sb.root / "legacy.json"
        p.write_text(json.dumps({
            "pr_limits": {"org/repo": 30},
            "delegate_codex_overrides": ["model_auto_compact_token_limit=300000"],
            "backlog_limit_kb": 50,
        }))
        loaded = config.load_config(p)
        self.assertEqual(loaded["notes_limit_kb"], 50)
        self.assertNotIn("pr_limits", loaded)
        self.assertNotIn("delegate_codex_overrides", loaded)


class TestBacklogCheck(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"user_names": ["Sam"], "open_ask_patterns": [r"\bin the inbox\b"]})

    def tearDown(self):
        self.sb.cleanup()

    def test_under_limits_exits_zero(self):
        self.sb.write_backlog(
            "demo",
            "# demo backlog\n\n## Boundary\nOwns demo.\n\n## Standing yes\n- Keep tests.\n\n## Open\n- Item one.\n\n## Done\n- 2026-11-15 Shipped.\n",
        )
        res = self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "demo")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("## Open", res.stdout)
        self.assertNotIn("OVER", res.stdout)

    def test_over_done_limit_and_cut_done(self):
        done_lines = "\n".join(f"- 2026-11-{(i % 18) + 1:02d} Item {i} " + ("x" * 200) for i in range(130))
        self.sb.write_backlog(
            "demo",
            f"# demo backlog\n\n## Boundary\nOwns demo.\n\n## Standing yes\n\n## Open\n- Open one.\n\n## Done\n{done_lines}\n",
        )
        res = self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "demo")
        self.assertEqual(res.returncode, 1)
        self.assertIn("OVER  Done", res.stdout)

        cut = self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "demo", "--cut-done")
        self.assertEqual(cut.returncode, 0, cut.stdout + cut.stderr)
        self.assertIn("deleted the", cut.stdout)
        self.assertTrue(list((self.sb.backlog_dir / "trims").glob("demo-done-cut-*.md")))
        done = backlog.sections((self.sb.backlog_dir / "demo-backlog.md").read_text())["## Done"]
        self.assertLessEqual(len(done.encode()), 10_000)
        self.assertGreater(len(done.encode()), 9_000)

    def test_ledger_clears_dropped_quote_and_ask(self):
        before = self.sb.root / "before.md"
        before.write_text(
            "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n- Always test.\n\n## Open\n"
            '- Item A (11-14: "Drop this"). Ask: keep old flag? Recommend drop.\n\n## Done\n'
        )
        self.sb.write_backlog(
            "demo",
            "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n- Always test.\n\n## Open\n- Item B.\n\n## Done\n- 11-15 Item A.\n",
        )
        terms = self.sb.root / "terminals.json"
        terms.write_text("[]")
        res = self.sb.run_script(
            COORD_SCRIPTS / "backlog_check.py", "demo", "--before", str(before), "--terminals", str(terms)
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("LOST  the user's words", res.stdout)
        self.assertIn("LOST  sentence that held an open ask", res.stdout)

        ledger = self.sb.root / "ledger.md"
        ledger.write_text(
            '- Dropped quote "Drop this" because Item A finished.\n'
            "- Dropped ask: Ask: keep old flag? (answered)\n"
        )
        res2 = self.sb.run_script(
            COORD_SCRIPTS / "backlog_check.py", "demo", "--before", str(before), "--ledger", str(ledger), "--terminals", str(terms)
        )
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)

    def test_cut_done_in_dirty_git_repo_uses_head_without_trims_file(self):
        done_lines = "\n".join(f"- 2026-11-{(i % 18) + 1:02d} Item {i} " + ("x" * 200) for i in range(130))
        backlog_path = self.sb.write_backlog(
            "demo",
            f"# demo backlog\n\n## Boundary\nOwns demo.\n\n## Standing yes\n\n## Open\n- Open one.\n\n## Done\n{done_lines}\n",
        )
        for cmd in (
            ["git", "-C", str(self.sb.backlog_dir), "init", "-q"],
            ["git", "-C", str(self.sb.backlog_dir), "config", "user.email", "test@example.com"],
            ["git", "-C", str(self.sb.backlog_dir), "config", "user.name", "Test"],
            ["git", "-C", str(self.sb.backlog_dir), "add", "demo-backlog.md"],
            ["git", "-C", str(self.sb.backlog_dir), "commit", "-q", "-m", "initial"],
        ):
            subprocess.run(cmd, check=True)
        # Modify ## Open so the file is dirty relative to HEAD while Done lines remain in HEAD
        backlog_path.write_text(backlog_path.read_text().replace("- Open one.", "- Open one.\n- Open two."))
        cut = self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "demo", "--cut-done")
        self.assertEqual(cut.returncode, 0, cut.stdout + cut.stderr)
        self.assertIn("Git keeps them at HEAD", cut.stdout)
        self.assertFalse(list((self.sb.backlog_dir / "trims").glob("demo-done-cut-*.md")))

    def test_ledger_lone_quote_on_one_line_does_not_swallow_next_line(self):
        before = self.sb.root / "before.md"
        before.write_text(
            "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n"
            '- Item A (11-14: "Drop this").\n\n## Done\n'
        )
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n\n## Done\n")
        terms = self.sb.root / "terminals.json"
        terms.write_text("[]")
        ledger = self.sb.root / "ledger.md"
        ledger.write_text('- stray quote " on line one\n- Dropped "Drop this" because done.\n')
        res = self.sb.run_script(
            COORD_SCRIPTS / "backlog_check.py", "demo", "--before", str(before), "--ledger", str(ledger), "--terminals", str(terms)
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_sections_parser(self):
        text = "# Title\n\n## Boundary\nA\n\n## Open\nB\n\n## Done\nC\n"
        secs = backlog.sections(text)
        self.assertIn("## Boundary", secs)
        self.assertIn("## Open", secs)
        self.assertIn("## Done", secs)


if __name__ == "__main__":
    unittest.main()

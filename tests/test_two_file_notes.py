"""Tests for a domain's notes in two files: resume.py, backlog_check.py and the files they read."""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from tests.helpers import COORD_SCRIPTS, Sandbox, add_script_paths

add_script_paths()
import backlog  # noqa: E402
import notes_files  # noqa: E402


def notes_file(open_items: str = "- **Item one.** Waits on PR #3.\n", extra: str = "") -> str:
    return ("# demo notes\n\n## Boundary\nOwns demo.\n\n## References\n- Later and Done: `demo-later.md`, beside this file.\n\n"
            f"## Standing yes\n- Keep tests (11-02: \"Keep them\").\n\n## Open\n{open_items}{extra}")


def later_file(later_items: str = "- **Parked.** Wakes on the user's yes.\n", done: str = "- 2026-11-15 Shipped.\n") -> str:
    return f"## Later\n\n{later_items}\n## Done\n\n{done}"


def done_lines(count: int, width: int = 200) -> str:
    return "\n".join(f"- 2026-11-{(i % 18) + 1:02d} Item {i} " + ("x" * width) for i in range(count)) + "\n"


RESUME = "import resume; print(resume.resume_text('demo', '<status>'))"


class TestTwoFileNotes(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.terms = self.sb.root / "terminals.json"
        self.terms.write_text("[]")

    def tearDown(self):
        self.sb.cleanup()

    def check(self, *args: str) -> subprocess.CompletedProcess:
        return self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "demo", *args)

    def resume(self) -> str:
        res = self.sb.run_python(RESUME)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def test_a_file_in_one_piece_resumes_with_its_three_sections(self):
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n- Yes.\n\n## Open\n- One.\n\n## Done\n- 11-15 Old.\n")
        out = self.resume()
        self.assertTrue(out.startswith(str(self.sb.backlog_dir / "demo-backlog.md")))
        self.assertIn("## Standing yes\n- Yes.\n\n## Open\n- One.", out)
        self.assertNotIn("Old.", out)
        self.assertNotIn("Later", out)

    def test_a_second_file_beside_a_notes_file_with_done_leaves_it_in_one_piece(self):
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Open\n- " + "y" * 30_000 + "\n\n## Done\n- 11-15 Old.\n")
        (self.sb.backlog_dir / "demo-later.md").write_text("## Later\n\n- From another domain.\n")
        res = self.check()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("CHECK  demo-later.md exists, but demo-backlog.md still has a ## Done section", res.stdout)
        out = self.resume()
        self.assertTrue(out.startswith(f"{self.sb.backlog_dir / 'demo-later.md'} exists"))
        self.assertNotIn("From another domain", out)

    def test_two_files_resume_prints_the_notes_and_every_later_line_or_which_are_hidden(self):
        later = "".join(f"- **Parked {i:02d}** " + "w" * 140 + "\n" for i in range(30))
        later += "- **Asks.** Ask: keep it or drop it? I recommend drop.\n"
        self.sb.write_notes("demo", notes_file(extra="- See ~/no-such-folder-for-this-test/x.md\n"), later_file(later))
        out = self.resume()
        self.assertIn("## References\n- Later and Done: `demo-later.md`", out)
        self.assertIn("- Asks. Ask: keep it or drop it? I recommend drop.", out)
        self.assertNotIn("Parked 00", out)
        self.assertIn("Parked 29", out)
        hidden = re.search(r"The (\d+) oldest of its 31 items are not shown here. They start on lines 3 to (\d+)\.", out)
        self.assertIsNotNone(hidden, out)
        shown = out.count("- Parked ")
        self.assertEqual(int(hidden.group(1)) + shown, 30)
        self.assertEqual(int(hidden.group(2)), 3 + int(hidden.group(1)) - 1)
        self.assertIn("Later items that hold an Ask:, which belongs in Open:\n- **Asks.** Ask: keep it", out)
        self.assertIn("1 local paths in demo-notes.md or demo-later.md no longer exist", out)
        self.assertNotIn("WARNING", out)

    def test_two_files_resume_over_its_limit_prints_the_notes_after_the_over_line(self):
        items = "".join(f"- **Item {i}.** " + "z" * (650 * i) + "\n" for i in range(1, 9))
        self.sb.write_notes("demo", notes_file(items), later_file())
        out = self.resume()
        self.assertTrue(out.startswith("OVER  the notes file is"), out[:200])
        first = out.splitlines()[0]
        self.assertIn("limit 20", first)
        self.assertLess(first.index('"Item 8.'), first.index('"Item 7.'))
        self.assertNotIn('"Item 5.', first)
        self.assertIn("- **Item 1.** zzz", out)

    def test_two_files_resume_warns_first_when_its_output_passes_30000_characters(self):
        Path(self.sb.config_path).write_text(json.dumps(dict(json.loads(self.sb.config_path.read_text()), notes_limit_kb=40)))
        self.sb.write_notes("demo", notes_file("- " + "q" * 31_000 + "\n"), later_file())
        out = self.resume()
        self.assertTrue(out.startswith("WARNING  this output is"), out[:100])

    def test_two_files_check_prints_both_files_against_their_own_limits(self):
        self.sb.write_notes("demo", notes_file(), later_file(done=done_lines(230)))
        res = self.check()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertRegex(res.stdout, r"KB  demo-notes.md in all, limit 20\n")
        self.assertRegex(res.stdout, r"\d+\.\d KB  ## Done, in demo-later.md")
        self.sb.write_notes("demo", notes_file(), later_file(done=done_lines(500)))
        res = self.check()
        self.assertEqual(res.returncode, 1)
        self.assertIn("OVER  Done is", res.stdout)
        self.assertIn("limit 100", res.stdout)

    def test_cut_done_in_git_cuts_the_second_file_to_half_and_keeps_uncommitted_lines(self):
        self.sb.write_notes("demo", notes_file(), later_file(done=done_lines(500)))
        self.sb.commit_all()
        later = self.sb.backlog_dir / "demo-later.md"
        later.write_text(later.read_text() + "- 2026-01-01 Added since the commit.\n")
        cut = self.check("--cut-done")
        self.assertIn("Git keeps them at HEAD", cut.stdout)
        text = later.read_text()
        self.assertIn("Added since the commit.", text)
        self.assertIn("## Later", text)
        self.assertLessEqual(len(backlog.sections(text)["## Done"].encode()), 50_000)
        self.assertFalse((self.sb.backlog_dir / "trims").exists())

    def test_cut_done_keeps_every_line_the_last_commit_lacks(self):
        self.sb.write_notes("demo", notes_file(), later_file(done="- 2026-11-01 Committed.\n"))
        self.sb.commit_all()
        later = self.sb.backlog_dir / "demo-later.md"
        later.write_text(later_file(done="- 2026-11-01 Committed.\n" + done_lines(300)))
        cut = self.check("--cut-done")
        self.assertIn("deleted the 1 oldest lines of Done", cut.stdout)
        self.assertIn("kept 300 items of Done that the last commit does not hold", cut.stdout)
        self.assertNotIn("Committed.", later.read_text())

    def test_since_reads_both_files_and_the_older_name(self):
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n- Merge on green (11-02: \"Yes\").\n\n"
                                      "## Open\n- **Parked.** Ask: keep it? I recommend yes.\n\n## Done\n- 11-15 Old.\n")
        self.sb.commit_all()
        self.sb.git("mv", "demo-backlog.md", "demo-notes.md")
        self.sb.write_notes("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n- Merge on green (11-02: \"Yes\").\n\n## Open\n",
                            "## Later\n\n- **Parked.** Ask: keep it? I recommend yes.\n\n## Done\n- 11-15 Old.\n")
        res = self.check("--since", "HEAD", "--terminals", str(self.terms))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("compared with HEAD: 0 lost", res.stdout)

    def test_a_standing_yes_entry_clears_only_on_the_users_dated_words(self):
        entry = '- Merge on green CI (11-02: "Yes, merge").'
        before = self.sb.root / "before.md"
        before.write_text(f"# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n{entry}\n\n## Open\n\n## Done\n")
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n\n## Done\n")
        ledger = self.sb.root / "ledger.md"
        ledger.write_text(f"{entry} Dropped: no longer needed.\n")
        res = self.check("--before", str(before), "--ledger", str(ledger), "--terminals", str(self.terms))
        self.assertIn("LOST  Standing yes entry: - Merge on green CI", res.stdout)
        ledger.write_text(f'{entry} Ended (11-20: "Ask me again first").\n')
        res = self.check("--before", str(before), "--ledger", str(ledger), "--terminals", str(self.terms))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_before_takes_a_copy_of_each_file(self):
        notes = notes_file("")
        self.sb.write_notes("demo", notes, later_file("- **Parked.** Ask: keep? I recommend yes.\n"))
        before_notes, before_later = self.sb.root / "notes.md", self.sb.root / "later.md"
        before_notes.write_text(notes)
        before_later.write_text(later_file("- **Parked.** Ask: keep? I recommend yes.\n- Gone (11-03: \"Drop it\").\n"))
        res = self.check("--before", str(before_notes), "--before", str(before_later), "--terminals", str(self.terms))
        self.assertIn('LOST  the user\'s words, 1 of 1 places: "Drop it"', res.stdout)
        self.assertNotIn("Ask: keep", res.stdout)
        res = self.sb.run_script(COORD_SCRIPTS / "backlog_check.py", "--before", str(before_notes), "demo",
                                 "--terminals", str(self.terms))
        self.assertNotIn("Drop it", res.stdout)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_dead_local_paths_are_checked_outside_done_and_code(self):
        here = self.sb.root
        (here / "Two words.md").write_text("")
        open_items = (f"- Exists: `{here}`, `{here}/Two words.md` and {here}/Two words.md, then more.\n"
                      f"- Wrapped: `{here}/Two\n  words.md`.\n"
                      f"- A command: `{COORD_SCRIPTS / 'fleet.py'} --domain demo`.\n"
                      "- Gone: ~/no-such-folder-for-this-test/a and `~/no-such-folder-for-this-test/b --flag`.\n"
                      f"- Gone too: {here}/no-such-file.md and `{here}/Gone file.md`.\n"
                      "- Not paths: /sync/v2/events, /ios/loop, /dev/composer-notices, /src/app/AGENTS.md, "
                      "[a link](missing.md) and ….\n"
                      "- A placeholder: ~/no-such-folder-for-this-test/<name>/x, `~/no-such-folder-for-this-test/a-….png` "
                      "and /compact.\n"
                      "```\n~/no-such-folder-for-this-test/in-code\n```\n")
        self.sb.write_notes("demo", notes_file(open_items), later_file(done="- 11-15 Done ~/no-such-folder-for-this-test/old\n"))
        res = self.check()
        checks = [line for line in res.stdout.splitlines() if line.startswith("CHECK")]
        self.assertCountEqual(checks, [
            f"CHECK  local path that does not exist: {path}, in demo-notes.md, ## Open"
            for path in ("~/no-such-folder-for-this-test/a", "~/no-such-folder-for-this-test/b", f"{here}/no-such-file.md",
                         f"{here}/Gone file.md")
        ])

    def test_notes_with_no_done_and_no_second_file_are_in_one_piece(self):
        notes = self.sb.write_notes("demo", notes_file("- " + "q" * 25_000 + "\n"))
        res = self.check()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertNotIn("CHECK", res.stdout)
        out = self.resume()
        self.assertTrue(out.startswith(f"{notes}\n\n## Boundary\nOwns demo."), out[:200])
        self.assertNotIn("Later", out)

    def test_a_done_heading_with_more_words_keeps_the_file_in_one_piece(self):
        self.sb.write_notes("demo", notes_file() + "\n## Done (newest first)\n" + done_lines(130), later_file())
        notes = backlog.Notes(notes_files.domain_files("demo", self.sb.backlog_dir))
        self.assertFalse(notes.two_files)
        self.assertTrue(notes.second_file_ignored)
        self.assertGreater(backlog.size(notes.done()), 20_000)
        self.assertLess(notes.read_at_start(), 1_000)

    def test_a_done_heading_inside_a_code_fence_is_text(self):
        fenced = "- Keep one.\n```\n## Done\n- 2020-01-01 An example.\n```\n- Keep two.\n"
        self.assertEqual(notes_files.section_text(f"## Open\n{fenced}## Done\n- Real.\n", "## Done"), "- Real.")
        path = self.sb.write_backlog("demo", notes_file(fenced) + "\n## Done\n" + done_lines(130))
        self.check("--cut-done")
        text = path.read_text()
        self.assertIn(fenced, text)
        self.assertLessEqual(backlog.size(backlog.done_of(text)), 10_000)

    def test_only_a_file_with_an_open_section_is_a_domain(self):
        folder = self.sb.backlog_dir
        (folder / "release-notes.md").write_text("# Release notes\n\n## 1.2\n- Fixes.\n")
        (folder / "ops-notes.md").write_text("# Ops notes\n\n```\n## Open\n```\n")
        (folder / "ops-backlog.md").write_text("# ops\n\n## Open\n- One.\n")
        self.sb.write_notes("demo", notes_file(), later_file())
        self.assertEqual(notes_files.domains_in(folder), ["demo", "ops"])
        ops = notes_files.domain_files("ops", folder)
        self.assertEqual((ops.notes, ops.stray), (folder / "ops-backlog.md", None))

    def test_a_separate_file_under_the_older_name_is_named_and_a_link_is_not(self):
        notes = self.sb.write_notes("demo", notes_file(), later_file())
        older = self.sb.backlog_dir / "demo-backlog.md"
        older.symlink_to(notes.name)
        self.assertNotIn("separate file", self.check().stdout)
        older.unlink()
        older.write_text("# demo\n\n## Open\n- An edit that missed the notes file.\n")
        self.assertIn(f"CHECK  {older} is a separate file from {notes}", self.check().stdout)
        self.assertTrue(self.resume().startswith(f"{older} is a separate file"))


if __name__ == "__main__":
    unittest.main()

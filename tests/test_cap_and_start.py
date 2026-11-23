"""Tests for delegate_cap.py and delegate_start.py."""
from __future__ import annotations

import json
import unittest

from tests.helpers import COORD_SCRIPTS, Sandbox


def make_terminal(addr: str, name: str | None, domain: str, status: str, role: str | None = None, space: str = "sp1") -> dict:
    labels = {"fleet.domain": domain, "herdr.space": space}
    if role:
        labels["fleet.role"] = role
    else:
        labels["fleet.task"] = name or addr
    return {
        "address": addr,
        "name": name,
        "cwd": "/tmp",
        "labels": labels,
        "status": {"state": status, "app": "claude-code"},
        "foreground": {"pid": 1000, "argv": ["claude"]},
        "turn": 1,
    }


class TestDelegateCap(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n\n## Done\n")

    def tearDown(self):
        self.sb.cleanup()

    def test_allows_under_cap_and_refuses_at_cap(self):
        terms = [
            make_terminal("t1", None, "demo", "done", role="coordinator"),
            make_terminal("t2", "d1", "demo", "working"),
            make_terminal("t3", "d2", "demo", "working"),
        ]
        tf = self.sb.root / "terms.json"
        tf.write_text(json.dumps(terms))
        res = self.sb.run_script(
            COORD_SCRIPTS / "delegate_cap.py", "--domain", "demo", "--terminals", str(tf),
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("ALLOWED", res.stdout)

        terms.append(make_terminal("t4", "d3", "demo", "working"))
        tf.write_text(json.dumps(terms))
        res2 = self.sb.run_script(
            COORD_SCRIPTS / "delegate_cap.py", "--domain", "demo", "--terminals", str(tf),
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(res2.returncode, 1)
        self.assertIn("REFUSED", res2.stdout)

    def test_an_unlabelled_delegate_named_only_in_later_still_counts(self):
        (self.sb.backlog_dir / "demo-backlog.md").unlink()
        self.sb.write_notes("demo", "# demo\n\n## Boundary\nOwns.\n\n## Open\n",
                            "## Later\n\n- Parked by mistake with its handle, session abcd1234.\n\n## Done\n")
        unlabelled = dict(make_terminal("t5", "d9", "demo", "working"), labels={"fleet.session": "abcd1234-0000"},
                          foreground={"argv": ["claude"]})
        tf = self.sb.root / "terms.json"
        tf.write_text(json.dumps([make_terminal("t1", None, "demo", "done", role="coordinator"), unlabelled]))
        res = self.sb.run_script(COORD_SCRIPTS / "delegate_cap.py", "--domain", "demo", "--terminals", str(tf))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("working  d9 t5", res.stdout)
        self.assertIn("1 of 3 working", res.stdout)

    def test_send_allowed_when_target_already_working_and_refused_for_idle_at_cap(self):
        send_log = self.sb.root / "sends.log"
        self.sb.write_stub("hc", f'echo "$@" >> "{send_log}"\necho "{{\\"ok\\":true}}"\n')
        terms = [
            make_terminal("t1", None, "demo", "done", role="coordinator"),
            make_terminal("t2", "d1", "demo", "working"),
            make_terminal("t3", "d2", "demo", "working"),
            make_terminal("t4", "d3", "demo", "working"),
            make_terminal("t5", "d4", "demo", "done"),
        ]
        tf = self.sb.root / "terms.json"
        tf.write_text(json.dumps(terms))

        # Target d1 is already working -> ALLOWED and sent
        ok = self.sb.run_script(
            COORD_SCRIPTS / "delegate_cap.py", "--domain", "demo", "--to", "d1", "--send", "hi", "--terminals", str(tf),
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("t2 hi", send_log.read_text())

        # Target d4 is idle at cap 3 -> REFUSED and NOT SENT
        before = send_log.read_text()
        ref = self.sb.run_script(
            COORD_SCRIPTS / "delegate_cap.py", "--domain", "demo", "--to", "d4", "--send", "go", "--terminals", str(tf),
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(ref.returncode, 1)
        self.assertIn("NOT SENT", ref.stdout)
        self.assertEqual(send_log.read_text(), before)

    def test_delegate_start_passes_delegate_env_to_hc_run(self):
        work = self.sb.root / "wt"
        work.mkdir()
        brief = self.sb.root / "BRIEF.md"
        brief.write_text("# Brief\n")
        run_log = self.sb.root / "hc_run.log"
        settings = json.loads(self.sb.config_path.read_text())
        settings["delegate_env"] = {"NODE_OPTIONS": "--max-old-space-size=4096"}
        self.sb.config_path.write_text(json.dumps(settings))
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        spawned = make_terminal("t2", "d1", "demo", "working")
        hc_script = f"""
case "$1" in
  ls)
    if [ -f "{self.sb.root}/spawned" ]; then
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t2","agent":"claude-code","status":{{"state":"working"}}}}]}}'
    else
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}}]}}'
    fi
    ;;
  get)
    if [ "$2" = "t1" ]; then
      echo '{json.dumps(coord)}'
    else
      echo '{json.dumps(spawned)}'
    fi
    ;;
  run)
    echo "$@" > "{run_log}"
    touch "{self.sb.root}/spawned"
    echo '{{"address":"t2"}}'
    ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        self.sb.write_stub("herdr-app", "echo '{}'\n")
        res = self.sb.run_script(
            COORD_SCRIPTS / "delegate_start.py", "d1", "--task", "task one", "--cwd", str(work),
            "--brief", str(brief), "--model", "sonnet",
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("env NODE_OPTIONS=--max-old-space-size=4096 claude", run_log.read_text())

    def test_removed_flags_rejected(self):
        cap_for_pr = self.sb.run_script(COORD_SCRIPTS / "delegate_cap.py", "--for-pr", "12")
        self.assertIn("unrecognized arguments", cap_for_pr.stderr)
        cap_asked = self.sb.run_script(COORD_SCRIPTS / "delegate_cap.py", "--user-asked", "do it")
        self.assertIn("unrecognized arguments", cap_asked.stderr)
        start_codex = self.sb.run_script(
            COORD_SCRIPTS / "delegate_start.py", "d1", "--task", "t", "--cwd", "/tmp",
            "--brief", "/tmp/b", "--agent", "codex",
        )
        self.assertIn("unrecognized arguments", start_codex.stderr)

    def test_delegate_end_refuses_a_delegate_with_an_open_finding_until_relayed(self):
        store = self.sb.root / "inbox_store.json"
        store.write_text(json.dumps({"s:sess-d1": {"ledger": {"items": [], "findings": [
            {"kind": "issue", "title": "Clock skips an hour", "detail": "", "id": 1, "at": 0}]}}}))
        self.sb.config_path.write_text(json.dumps({**json.loads(self.sb.config_path.read_text()),
                                                   "inbox_store": str(store)}))
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        delegate = make_terminal("t2", "d1", "demo", "done")
        delegate["labels"]["fleet.session"] = "sess-d1"
        hc_script = f"""
case "$1 $2" in
  "ls "*) echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t2","agent":"claude-code","status":{{"state":"done"}}}}]}}' ;;
  "get t1") echo '{json.dumps(coord)}' ;;
  "get t2") echo '{json.dumps(delegate)}' ;;
  *) echo "unexpected hc $*" >&2; exit 9 ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        res = self.sb.run_script(COORD_SCRIPTS / "delegate_end.py", "d1", "--now",
                                 env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("finding: Clock skips an hour", res.stdout)
        self.assertIn("--relayed", res.stdout)

    def test_delegate_end_refuses_legacy_codex_record(self):
        ended_dir = self.sb.state_dir / "ended"
        ended_dir.mkdir(parents=True)
        work = self.sb.root / "wt"
        work.mkdir()
        (ended_dir / "codex-sess-1.json").write_text(json.dumps({
            "name": "old-codex",
            "task": "lint",
            "domain": "demo",
            "session": "codex-sess-1",
            "program": "codex",
            "cwd": str(work),
            "ended_at": "2026-11-15T00:00:00+00:00",
        }))
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        hc_script = f"""
case "$1" in
  ls) echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}}]}}' ;;
  get) echo '{json.dumps(coord)}' ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        res = self.sb.run_script(
            COORD_SCRIPTS / "delegate_end.py", "--resume", "old-codex",
            env_extra={"HC_TERMINAL_ID": "t1"},
        )
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("codex resume codex-sess-1", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()

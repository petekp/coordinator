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

    def test_delegate_end_preserves_effort_and_zero_turn_model_on_resume(self):
        work = self.sb.root / "wt"
        work.mkdir()
        run_log = self.sb.root / "hc_runs.log"
        settings = json.loads(self.sb.config_path.read_text())
        settings["delegate_claude_args"] = ["--autocompact", "300000"]
        self.sb.config_path.write_text(json.dumps(settings))
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        delegate = make_terminal("t2", "d1", "demo", "done")
        delegate["cwd"] = str(work)
        delegate["labels"].update({"fleet.session": "sess-zero", "fleet.model": "sonnet", "fleet.effort": "low", "herdr.tab": "tab1", "where": "sp1 / demo [task one]"})
        resumed = make_terminal("t3", "d1", "demo", "working")
        hc_script = f"""
case "$1" in
  ls)
    if [ -f "{self.sb.root}/ended" ] && [ ! -f "{self.sb.root}/resumed" ]; then
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}}]}}'
    elif [ -f "{self.sb.root}/resumed" ]; then
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t3","agent":"claude-code","status":{{"state":"working"}}}}]}}'
    else
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t2","agent":"claude-code","status":{{"state":"done"}}}}]}}'
    fi
    ;;
  get)
    if [ "$2" = "t1" ]; then echo '{json.dumps(coord)}'
    elif [ "$2" = "t2" ] && [ ! -f "{self.sb.root}/ended" ]; then echo '{json.dumps(delegate)}'
    elif [ "$2" = "t2" ]; then exit 3
    else echo '{json.dumps(resumed)}'
    fi
    ;;
  read) echo "done" ;;
  kill|rm) touch "{self.sb.root}/ended"; echo '{{}}' ;;
  run)
    echo "$@" >> "{run_log}"
    touch "{self.sb.root}/resumed"
    echo '{{"address":"t3"}}'
    ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        self.sb.write_stub("herdr-app", "echo '{}'\n")
        end_res = self.sb.run_script(
            COORD_SCRIPTS / "delegate_end.py", "d1", "--now",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(end_res.returncode, 0, end_res.stdout + end_res.stderr)
        saved = json.loads((self.sb.state_dir / "ended" / "sess-zero.json").read_text())
        self.assertEqual(saved["model"], "sonnet")
        self.assertEqual(saved["effort"], "low")
        self.assertIsNone(saved["quiet_since"])

        res_zero = self.sb.run_script(
            COORD_SCRIPTS / "delegate_end.py", "--resume", "d1",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(res_zero.returncode, 0, res_zero.stdout + res_zero.stderr)
        launched_zero = run_log.read_text()
        self.assertIn("--resume sess-zero --model sonnet --effort low --autocompact 300000", launched_zero)
        self.assertIn("fleet.model=sonnet", launched_zero)
        self.assertIn("fleet.effort=low", launched_zero)

        (self.sb.root / "resumed").unlink()
        run_log.write_text("")
        (self.sb.state_dir / "ended" / "sess-turned.json").write_text(json.dumps({
            "name": "d2",
            "task": "task two",
            "domain": "demo",
            "session": "sess-turned",
            "program": "claude",
            "model": "sonnet",
            "effort": "low",
            "cwd": str(work),
            "ended_at": "2026-11-23T00:00:00+00:00",
            "quiet_since": "2026-11-22T00:00:00+00:00",
        }))
        res_turned = self.sb.run_script(
            COORD_SCRIPTS / "delegate_end.py", "--resume", "d2",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(res_turned.returncode, 0, res_turned.stdout + res_turned.stderr)
        launched_turned = run_log.read_text()
        self.assertIn("--resume sess-turned --effort low --autocompact 300000", launched_turned)
        self.assertNotIn("--model sonnet", launched_turned)

    def test_delegate_start_resume_accepts_effort_and_zero_turn_model(self):
        work = self.sb.root / "wt"
        work.mkdir()
        proj = self.sb.root / ".claude" / "projects" / "demo"
        proj.mkdir(parents=True)
        (proj / "sess-crash.jsonl").write_text('{"type":"user","timestamp":"2026-11-23T00:00:00Z"}\n')
        (proj / "sess-ok.jsonl").write_text(
            '{"type":"assistant","timestamp":"2026-11-23T00:00:00Z","message":{"model":"claude-sonnet-5-5","usage":{"input_tokens":1}}}\n'
        )
        run_log = self.sb.root / "start_resume.log"
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        spawned = make_terminal("t2", "d1", "demo", "working")
        hc_script = f"""
case "$1" in
  ls)
    if [ -f "{self.sb.root}/up" ]; then
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t2","agent":"claude-code","status":{{"state":"working"}}}}]}}'
    else
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}}]}}'
    fi
    ;;
  get)
    if [ "$2" = "t1" ]; then echo '{json.dumps(coord)}'; else echo '{json.dumps(spawned)}'; fi
    ;;
  run)
    echo "$@" >> "{run_log}"
    touch "{self.sb.root}/up"
    echo '{{"address":"t2"}}'
    ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        self.sb.write_stub("herdr-app", "echo '{}'\n")
        r1 = self.sb.run_script(
            COORD_SCRIPTS / "delegate_start.py", "d1", "--task", "recover", "--cwd", str(work),
            "--resume", "sess-crash", "--model", "sonnet", "--effort", "xhigh",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn("--resume sess-crash --model sonnet --effort xhigh", run_log.read_text())
        self.assertIn("sonnet xhigh; resumed", r1.stdout)

        (self.sb.root / "up").unlink()
        run_log.write_text("")
        ended_dir = self.sb.state_dir / "ended"
        ended_dir.mkdir(parents=True, exist_ok=True)
        (ended_dir / "sess-ok.json").write_text(json.dumps({
            "name": "d1", "task": "recover", "domain": "demo", "session": "sess-ok",
            "program": "claude", "model": "sonnet", "effort": "xhigh", "cwd": str(work),
            "ended_at": "2026-11-23T00:00:00+00:00",
        }))
        r2 = self.sb.run_script(
            COORD_SCRIPTS / "delegate_start.py", "d1", "--task", "recover", "--cwd", str(work),
            "--resume", "sess-ok",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("--resume sess-ok --effort xhigh", run_log.read_text())
        self.assertIn("fleet.model=sonnet", run_log.read_text())
        self.assertIn("fleet.effort=xhigh", run_log.read_text())
        self.assertNotIn("--model sonnet", run_log.read_text())
        self.assertIn("sonnet xhigh; resumed", r2.stdout)
        self.assertFalse((ended_dir / "sess-ok.json").exists())


if __name__ == "__main__":
    unittest.main()

"""A delegate started in a live terminal that reports no status is kept."""
from __future__ import annotations

import json
import unittest

from tests.helpers import COORD_SCRIPTS, Sandbox
from tests.test_cap_and_start import make_terminal


class TestStartWithoutStatus(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.write_backlog("demo", "# demo\n\n## Boundary\nOwns.\n\n## Standing yes\n\n## Open\n\n## Done\n")

    def tearDown(self):
        self.sb.cleanup()

    def test_delegate_start_with_live_terminal_and_no_status_succeeds(self):
        work = self.sb.root / "wt"
        work.mkdir()
        brief = self.sb.root / "BRIEF.md"
        brief.write_text("# Brief\n")
        coord = make_terminal("t1", None, "demo", "done", role="coordinator")
        silent = dict(make_terminal("t2", "d1", "demo", "working"), status=None, state="live", exit_code=None)
        hc_script = f"""
case "$1" in
  ls)
    if [ -f "{self.sb.root}/spawned" ]; then
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}},{{"address":"t2","status":null}}]}}'
    else
      echo '{{"terminals":[{{"address":"t1","agent":"claude-code","status":{{"state":"done"}}}}]}}'
    fi
    ;;
  get)
    if [ "$2" = "t1" ]; then echo '{json.dumps(coord)}'; else echo '{json.dumps(silent)}'; fi
    ;;
  run)
    touch "{self.sb.root}/spawned"
    echo '{{"address":"t2"}}'
    ;;
esac
"""
        self.sb.write_stub("hc", hc_script)
        self.sb.write_stub("herdr-app", "echo '{}'\n")
        res = self.sb.run_python(
            "import fleet; fleet.SECONDS_TO_WAIT_FOR_AGENT = 2\n"
            "import delegate_start\n"
            f"sys.argv = ['delegate_start.py', 'd1', '--task', 'x', '--cwd', {str(work)!r}, "
            f"'--brief', {str(brief)!r}, '--model', 'sonnet']\n"
            "sys.exit(delegate_start.main())\n",
            env_extra={"HC_TERMINAL_ID": "t1", "HOME": str(self.sb.root)},
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("started d1 as terminal t2", res.stdout)
        self.assertIn("has not reported a status", res.stdout)
        self.assertNotIn("hc rm", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()

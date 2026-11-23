"""Shared test fixtures: temp config/state dirs and stub hc/gh binaries."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORD_SCRIPTS = ROOT / "skills" / "coordinator" / "scripts"
SWEEP_SCRIPTS = ROOT / "skills" / "fleet-sweep" / "scripts"


def add_script_paths() -> None:
    for p in (str(COORD_SCRIPTS), str(SWEEP_SCRIPTS)):
        if p not in sys.path:
            sys.path.insert(0, p)


class Sandbox:
    """Isolated temp workspace with config.json, backlog_dir, state_dir, and stub PATH."""

    def __init__(self, config_overrides: dict | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.backlog_dir = self.root / "backlogs"
        self.backlog_dir.mkdir()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.config_path = self.root / "config.json"
        cfg = {
            "backlog_dir": str(self.backlog_dir),
            "state_dir": str(self.state_dir),
            "check_for_updates": False,
        }
        if config_overrides:
            cfg.update(config_overrides)
        self.config_path.write_text(json.dumps(cfg))

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def write_stub(self, name: str, script: str) -> Path:
        path = self.bin_dir / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def write_backlog(self, domain: str, text: str) -> Path:
        path = self.backlog_dir / f"{domain}-backlog.md"
        path.write_text(text)
        return path

    def write_notes(self, domain: str, text: str, later: str | None = None) -> Path:
        path = self.backlog_dir / f"{domain}-notes.md"
        path.write_text(text)
        if later is not None:
            (self.backlog_dir / f"{domain}-later.md").write_text(later)
        return path

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.backlog_dir), *args], check=True, capture_output=True)

    def commit_all(self, message: str = "save") -> None:
        if not (self.backlog_dir / ".git").exists():
            self.git("init", "-q")
            self.git("config", "user.email", "test@example.com")
            self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_python(self, code: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(COORD_SCRIPTS)!r})\n{code}"],
            capture_output=True,
            text=True,
            env=self.env(env_extra),
            cwd=str(self.root),
            timeout=30,
        )

    def env(self, extra: dict | None = None) -> dict:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"
        env["COORDINATOR_CONFIG"] = str(self.config_path)
        env.pop("CLAUDECODE", None)
        if extra:
            env.update(extra)
        return env

    def run_script(self, path: Path, *args: str, env_extra: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(path), *args],
            capture_output=True,
            text=True,
            env=self.env(env_extra),
            cwd=str(cwd or self.root),
            timeout=30,
        )

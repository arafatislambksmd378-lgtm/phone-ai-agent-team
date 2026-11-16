"""Workspace sandbox — confinement checks + git checkpoints (never touches host repo)."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path


class Sandbox:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _git(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "PhoneAgent", "GIT_AUTHOR_EMAIL": "agent@local",
            "GIT_COMMITTER_NAME": "PhoneAgent", "GIT_COMMITTER_EMAIL": "agent@local",
            "GIT_TERMINAL_PROMPT": "0",
        }
        return subprocess.run(["git", *args], cwd=str(self.workspace), capture_output=True,
                              text=True, timeout=timeout, env=env)

    def is_repo(self) -> bool:
        return (self.workspace / ".git").exists()

    def init_git(self) -> bool:
        try:
            if not self.is_repo():
                self._git(["init", "-q"])
            self._git(["config", "user.email", "agent@local"])
            self._git(["config", "user.name", "PhoneAgent"])
            return True
        except Exception:
            return False

    def checkpoint(self, message: str) -> bool:
        """Commit current state; returns False when there is nothing to commit."""
        try:
            if not self.is_repo():
                self.init_git()
            self._git(["add", "-A"])
            if not self._git(["status", "--porcelain"]).stdout.strip():
                return False
            return self._git(["commit", "-q", "-m", message[:200]]).returncode == 0
        except Exception:
            return False

    def history(self, limit: int = 20) -> list[str]:
        try:
            out = self._git(["log", f"-{limit}", "--pretty=%h %s"]).stdout
            return [l for l in out.splitlines() if l.strip()]
        except Exception:
            return []

    def is_inside(self, path: str) -> bool:
        """True only if `path` is inside the workspace (sibling prefixes rejected)."""
        try:
            root = os.path.realpath(self.workspace)
            target = os.path.realpath(path)
            return target == root or os.path.commonpath([root, target]) == root
        except (ValueError, OSError):
            return False

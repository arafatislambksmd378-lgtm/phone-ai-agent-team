"""Workspace sandbox — isolation checks + git checkpointing."""
from __future__ import annotations
import subprocess
from pathlib import Path

class Sandbox:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def init_git(self) -> bool:
        try:
            subprocess.run(["git", "init"], cwd=str(self.workspace), capture_output=True, timeout=15)
            subprocess.run(["git", "config", "user.email", "agent@local"], cwd=str(self.workspace), capture_output=True, timeout=10)
            subprocess.run(["git", "config", "user.name", "PhoneAgent"], cwd=str(self.workspace), capture_output=True, timeout=10)
            return True
        except Exception:
            return False

    def checkpoint(self, message: str) -> bool:
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(self.workspace), capture_output=True, timeout=30)
            r = subprocess.run(["git", "commit", "-m", message], cwd=str(self.workspace), capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    def is_inside(self, path: str) -> bool:
        try:
            p = Path(path).resolve()
            return str(p).startswith(str(self.workspace))
        except Exception:
            return False

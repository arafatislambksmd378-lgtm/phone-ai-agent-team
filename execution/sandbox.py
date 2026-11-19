"""Workspace sandbox — confinement checks, secret scanning, branch & git checkpoints."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path

from security.secret_manager import contains_secret

DEFAULT_GITIGNORE = """__pycache__/
*.py[cod]
.pytest_cache/
.env
.env.*
*.part
*.tmp
"""

SECRET_FILE_NAMES = {".env", "id_rsa", "id_ed25519", ".git-credentials", ".netrc"}
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")


class Sandbox:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _git(self, args: list[str], timeout: int = 30,
             extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self.workspace)),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "GIT_AUTHOR_NAME": "PhoneAgent",
            "GIT_AUTHOR_EMAIL": "agent@local",
            "GIT_COMMITTER_NAME": "PhoneAgent",
            "GIT_COMMITTER_EMAIL": "agent@local",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", *args], cwd=str(self.workspace), capture_output=True,
                              text=True, timeout=timeout, env=env)

    def is_repo(self) -> bool:
        return (self.workspace / ".git").exists()

    def init_git(self, default_branch: str = "main") -> bool:
        try:
            if not self.is_repo():
                r = self._git(["init", "-q", "-b", default_branch])
                if r.returncode != 0:
                    self._git(["init", "-q"])
            self._git(["config", "user.email", "agent@local"])
            self._git(["config", "user.name", "PhoneAgent"])
            gi = self.workspace / ".gitignore"
            if not gi.exists():
                gi.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
            return True
        except Exception:
            return False

    def scan_secrets(self) -> list[dict]:
        """Scan workspace files for accidental secrets or private keys before commit/publish."""
        findings: list[dict] = []
        for f in sorted(self.workspace.rglob("*")):
            if not f.is_file() or ".git" in f.parts or "__pycache__" in f.parts:
                continue
            rel = str(f.relative_to(self.workspace))
            if f.name in SECRET_FILE_NAMES:
                findings.append({"file": rel, "reason": "restricted secret file"})
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_bytes().decode("utf-8", errors="ignore")
            except OSError:
                continue
            if PRIVATE_KEY_RE.search(text) or contains_secret(text):
                findings.append({"file": rel, "reason": "detected secret/token pattern"})
        return findings

    def checkpoint(self, message: str) -> bool:
        """Safely stage & commit current workspace state; returns False when nothing changed."""
        return bool(self.checkpoint_commit(message))

    def checkpoint_commit(self, message: str) -> str:
        """Create a safe checkpoint commit and return its short SHA (or '' if no changes)."""
        try:
            if not self.is_repo():
                self.init_git()
            # Stage everything covered by .gitignore, then unstage any restricted secret files
            self._git(["add", "-A"])
            for secret_name in SECRET_FILE_NAMES:
                if (self.workspace / secret_name).exists():
                    self._git(["reset", "-q", "HEAD", "--", secret_name])
            if not self._git(["status", "--porcelain"]).stdout.strip():
                return ""
            clean_msg = (message or "checkpoint").replace("\n", " ").strip()[:200]
            if self._git(["commit", "-q", "-m", clean_msg]).returncode != 0:
                return ""
            return self._git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        except Exception:
            return ""

    def current_branch(self) -> str:
        if not self.is_repo():
            return ""
        out = self._git(["branch", "--show-current"]).stdout.strip()
        if out:
            return out
        return self._git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()

    def list_branches(self) -> list[str]:
        if not self.is_repo():
            return []
        out = self._git(["branch", "--format=%(refname:short)"]).stdout
        return [b.strip() for b in out.splitlines() if b.strip()]

    def create_branch(self, branch_name: str, checkout: bool = True) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,80}", branch_name or "") or ".." in branch_name:
            return False
        if not self.is_repo():
            self.init_git()
        if not self.history(limit=1):
            self.checkpoint("initial workspace")
        args = ["checkout", "-b", branch_name] if checkout else ["branch", branch_name]
        return self._git(args).returncode == 0

    def checkout_branch(self, branch_name: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,80}", branch_name or ""):
            return False
        return self._git(["checkout", branch_name]).returncode == 0

    def restore_checkpoint(self, commit_ref: str) -> bool:
        """Restore workspace files from a prior checkpoint commit without rewriting history."""
        if not re.fullmatch(r"[0-9a-fA-F]{4,40}|HEAD(?:~\d+)?", commit_ref or ""):
            return False
        return self._git(["checkout", commit_ref, "--", "."]).returncode == 0

    def history(self, limit: int = 20) -> list[str]:
        try:
            out = self._git(["log", f"-{int(limit)}", "--pretty=%h %s"]).stdout
            return [l for l in out.splitlines() if l.strip()]
        except Exception:
            return []

    def conflict_files(self) -> list[str]:
        if not self.is_repo():
            return []
        out = self._git(["diff", "--name-only", "--diff-filter=U"]).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]

    def is_inside(self, path: str) -> bool:
        """True only if `path` is inside the workspace (sibling prefixes rejected)."""
        try:
            root = os.path.realpath(self.workspace)
            target = os.path.realpath(path)
            return target == root or os.path.commonpath([root, target]) == root
        except (ValueError, OSError):
            return False

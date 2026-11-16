"""GitHub integration — read-only status by default; writes always need approval."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

DEFAULT_REPO = "arafatislambksmd378-lgtm/phone-ai-agent-team"


class GitHubClient:
    """Local git inspection + public repository metadata.

    No token is stored or required for status. Any mutating operation
    (commit/push) is *not* performed here: it must go through the approval
    workflow first (see ApprovalManager), per the project's safety contract.
    """

    def __init__(self, repo: str = DEFAULT_REPO, token: str | None = None):
        self.repo = repo
        self.token = token or os.getenv("GITHUB_TOKEN", "")

    # ---------- local git ----------
    def _git(self, args: list[str], cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)

    def status(self, cwd: str = ".") -> dict:
        try:
            branch = self._git(["branch", "--show-current"], cwd).stdout.strip()
            porcelain = self._git(["status", "--porcelain"], cwd).stdout
            ahead = self._git(["rev-list", "--count", "@{u}..HEAD"], cwd).stdout.strip()
            return {"ok": True, "branch": branch, "dirty": bool(porcelain.strip()),
                    "changed_files": len([l for l in porcelain.splitlines() if l.strip()]),
                    "unpushed_commits": int(ahead) if ahead.isdigit() else None}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def log(self, cwd: str = ".", limit: int = 10) -> dict:
        try:
            out = self._git(["log", f"-{limit}", "--pretty=%h|%s"], cwd).stdout
            return {"ok": True, "commits": [l for l in out.splitlines() if "|" in l]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- remote metadata ----------
    def repo_info(self) -> dict:
        url = f"https://api.github.com/repos/{self.repo}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "phone-ai-agent-team"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            return {"ok": True, "full_name": d.get("full_name"),
                    "default_branch": d.get("default_branch"),
                    "private": d.get("private"), "pushed_at": d.get("pushed_at")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

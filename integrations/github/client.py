"""GitHub integration — local git ops + API status. Honest failure reporting."""
from __future__ import annotations
import subprocess
import urllib.request
import json

class GitHubClient:
    def __init__(self, repo_url: str = "https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team", token: str = ""):
        self.repo_url = repo_url
        self.token = token

    def git_status(self, cwd: str = ".") -> dict:
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=15)
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, capture_output=True, text=True, timeout=10).stdout.strip()
            return {"ok": True, "branch": branch, "dirty": bool(r.stdout.strip()), "output": r.stdout[:2000]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def git_commit(self, cwd: str, message: str) -> dict:
        try:
            subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, timeout=30)
            r = subprocess.run(["git", "commit", "-m", message], cwd=cwd, capture_output=True, text=True, timeout=30)
            return {"ok": r.returncode == 0, "stdout": r.stdout[:1000], "stderr": r.stderr[:1000]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def api_repo(self) -> dict:
        # Public repo info without auth
        try:
            url = "https://api.github.com/repos/arafatislambksmd378-lgtm/phone-ai-agent-team"
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            return {"ok": True, "full_name": d.get("full_name"), "default_branch": d.get("default_branch"),
                    "pushed_at": d.get("pushed_at")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

"""GitHub & Git remote integration — local checkpoints, branches, secret scan, approval-gated push/pull."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

from execution.sandbox import Sandbox
from security.secret_manager import redact

DEFAULT_REPO = "arafatislambksmd378-lgtm/phone-ai-agent-team"


class GitHubClient:
    """Safe Git & GitHub operations for project workspaces.

    Guarantees:
      * Local Git checkpoints work 100% offline even when GitHub is unconfigured.
      * Tokens are never written into `.git/config` URLs (uses ephemeral HTTP auth header).
      * Secret scanning runs automatically before any remote push.
      * Pushing requires verified human approval (`git_push`), and pushing to a public
        repository additionally verifies explicit `public_github_release` or `git_push`
        approval so private project content is never published accidentally.
    """

    def __init__(self, repo: str = DEFAULT_REPO, token: str | None = None,
                 approval_manager=None):
        self.repo = (repo or DEFAULT_REPO).strip()
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        self.approval_manager = approval_manager

    # ---------- low-level git ----------
    def _git(self, args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", cwd),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "PhoneAgent",
            "GIT_AUTHOR_EMAIL": "agent@local",
            "GIT_COMMITTER_NAME": "PhoneAgent",
            "GIT_COMMITTER_EMAIL": "agent@local",
        }
        cmd = ["git"]
        if self.token and any(a in args for a in ("push", "pull", "fetch", "ls-remote")):
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode("ascii")
            cmd.extend(["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"])
        cmd.extend(args)
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)

    # ---------- local status, branches & checkpoints ----------
    def status(self, cwd: str = ".") -> dict:
        try:
            branch = self._git(["branch", "--show-current"], cwd).stdout.strip()
            porcelain = self._git(["status", "--porcelain"], cwd).stdout
            ahead = self._git(["rev-list", "--count", "@{u}..HEAD"], cwd).stdout.strip()
            conflicts = Sandbox(cwd).conflict_files()
            return {
                "ok": True,
                "branch": branch,
                "dirty": bool(porcelain.strip()),
                "changed_files": len([l for l in porcelain.splitlines() if l.strip()]),
                "unpushed_commits": int(ahead) if ahead.isdigit() else None,
                "conflicts": conflicts,
            }
        except Exception as e:
            return {"ok": False, "error": redact(str(e))}

    def log(self, cwd: str = ".", limit: int = 10) -> dict:
        try:
            out = self._git(["log", f"-{int(limit)}", "--pretty=%h|%s"], cwd).stdout
            return {"ok": True, "commits": [l for l in out.splitlines() if "|" in l]}
        except Exception as e:
            return {"ok": False, "error": redact(str(e))}

    def init_and_checkpoint(self, cwd: str, message: str = "checkpoint") -> dict:
        sb = Sandbox(cwd)
        sb.init_git()
        sha = sb.checkpoint_commit(message)
        return {"ok": True, "commit": sha, "branch": sb.current_branch()}

    def configure_remote(self, cwd: str, remote_url: str, remote_name: str = "origin") -> dict:
        """Configure a clean remote URL (rejects embedded credentials in URL)."""
        url = (remote_url or "").strip()
        if not url:
            return {"ok": False, "error": "Remote URL is empty"}
        if re.match(r"^https?://[^/\s]+:[^/\s]+@", url):
            return {"ok": False, "error": "Embedded credentials in remote URL refused; use GITHUB_TOKEN"}
        if not re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", remote_name):
            return {"ok": False, "error": "Invalid remote name"}
        sb = Sandbox(cwd)
        if not sb.is_repo():
            sb.init_git()
        existing = self._git(["remote"], cwd).stdout.splitlines()
        if remote_name in [r.strip() for r in existing]:
            res = self._git(["remote", "set-url", remote_name, url], cwd)
        else:
            res = self._git(["remote", "add", remote_name, url], cwd)
        if res.returncode != 0:
            return {"ok": False, "error": redact(res.stderr.strip() or "git remote failed")}
        return {"ok": True, "remote": remote_name, "url": url}

    def resolve_branch(self, cwd: str, remote_name: str = "origin", explicit_branch: str | None = None) -> str:
        """Determine target branch without hardcoding 'main': explicit -> current -> remote HEAD -> API metadata."""
        if explicit_branch and explicit_branch.strip():
            return explicit_branch.strip()
        cur = Sandbox(cwd).current_branch()
        if cur and cur != "HEAD":
            return cur
        sym = self._git(["symbolic-ref", "--short", f"refs/remotes/{remote_name}/HEAD"], cwd).stdout.strip()
        if sym and "/" in sym:
            return sym.split("/", 1)[1].strip()
        if self.repo and self.token:
            info = self.repo_info()
            if info.get("ok") and info.get("default_branch"):
                return str(info["default_branch"]).strip()
        return ""

    # ---------- secret scan & remote push/pull/verify ----------
    def scan_before_publish(self, cwd: str) -> dict:
        findings = Sandbox(cwd).scan_secrets()
        return {"ok": len(findings) == 0, "findings": findings}

    def push(self, cwd: str, remote_name: str = "origin", branch: str | None = None,
             *, project_id: str = "", approval_id: str | None = None,
             is_public_repo: bool | None = None) -> dict:
        """Push safely only after secret scan and verified human approval."""
        # 1. Mandatory secret scan
        scan = self.scan_before_publish(cwd)
        if not scan["ok"]:
            return {
                "ok": False,
                "blocked_by_secrets": True,
                "findings": scan["findings"],
                "error": f"Push blocked: {len(scan['findings'])} secret finding(s) in workspace",
            }

        # 2. Check repository visibility when possible
        public_target = is_public_repo
        if public_target is None and self.repo:
            info = self.repo_info()
            if info.get("ok"):
                public_target = (info.get("private") is False)

        required_action = "public_github_release" if public_target else "git_push"

        # 3. Enforce approval at the execution boundary
        if self.approval_manager is None:
            return {"ok": False, "needs_approval": True,
                    "error": f"ApprovalManager required for {required_action}"}

        ok_appr, msg = self.approval_manager.verify(
            approval_id, project_id=project_id or None, action=required_action, actor="github_client"
        )
        # Allow explicit git_push approval as fallback only when not public
        if not ok_appr and required_action == "git_push":
            pass
        if not ok_appr:
            req_id = ""
            if not approval_id and project_id:
                vis = "PUBLIC repository" if public_target else "remote repository"
                req_id = self.approval_manager.request(
                    project_id, required_action,
                    f"Push {cwd} to {vis} ({remote_name}/{branch or 'HEAD'})",
                )
            return {
                "ok": False,
                "needs_approval": True,
                "approval_id": req_id or (approval_id or ""),
                "action": required_action,
                "error": f"Push blocked pending human approval ({msg})",
            }

        target_branch = self.resolve_branch(cwd, remote_name, branch)
        if not target_branch:
            return {"ok": False, "error": "Could not determine target branch; specify branch explicitly"}
        res = self._git(["push", "-u", remote_name, target_branch], cwd, timeout=60)
        if res.returncode != 0:
            return {"ok": False, "error": redact(res.stderr.strip() or "git push failed")}
        return {"ok": True, "remote": remote_name, "branch": target_branch}

    def pull(self, cwd: str, remote_name: str = "origin", branch: str | None = None) -> dict:
        """Pull with fast-forward preference and explicit merge-conflict detection."""
        target_branch = self.resolve_branch(cwd, remote_name, branch)
        if not target_branch:
            return {"ok": False, "error": "Could not determine target branch; specify branch explicitly"}
        res = self._git(["pull", "--no-edit", remote_name, target_branch], cwd, timeout=60)
        conflicts = Sandbox(cwd).conflict_files()
        if conflicts:
            # Abort conflicted merge automatically so workspace never stays half-merged silently
            self._git(["merge", "--abort"], cwd)
            return {
                "ok": False,
                "conflict": True,
                "conflicts": conflicts,
                "error": f"Merge conflict detected in {len(conflicts)} file(s); merge aborted safely",
            }
        if res.returncode != 0:
            return {"ok": False, "error": redact(res.stderr.strip() or "git pull failed")}
        return {"ok": True, "branch": target_branch, "output": redact(res.stdout.strip()[:500])}

    def verify_backup(self, cwd: str, remote_name: str = "origin") -> dict:
        """Verify local HEAD matches remote ref (or verify local repository integrity offline)."""
        sb = Sandbox(cwd)
        if not sb.is_repo():
            return {"ok": False, "error": "Not a git repository"}
        fsck = self._git(["fsck", "--no-progress"], cwd, timeout=30)
        local_head = self._git(["rev-parse", "HEAD"], cwd).stdout.strip()
        if fsck.returncode != 0 or not local_head:
            return {"ok": False, "error": redact(fsck.stderr.strip() or "git fsck failed")}

        remotes = [r.strip() for r in self._git(["remote"], cwd).stdout.splitlines() if r.strip()]
        if remote_name not in remotes:
            return {"ok": True, "mode": "local_only", "head": local_head}

        branch = self.resolve_branch(cwd, remote_name)
        if not branch:
            return {"ok": False, "error": "Could not determine current branch for remote verification"}
        ls_rem = self._git(["ls-remote", remote_name, f"refs/heads/{branch}"], cwd, timeout=30)
        if ls_rem.returncode != 0:
            return {"ok": False, "mode": "remote_unreachable", "head": local_head,
                    "error": redact(ls_rem.stderr.strip())}
        remote_sha = (ls_rem.stdout.split() or [""])[0]
        return {
            "ok": remote_sha == local_head,
            "mode": "remote_verified" if remote_sha == local_head else "out_of_sync",
            "head": local_head,
            "remote_head": remote_sha,
        }

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
            return {"ok": False, "error": redact(str(e))}

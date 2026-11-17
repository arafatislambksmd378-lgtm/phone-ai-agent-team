"""GitHub-centric execution & workspace service:
  * Dynamic default-branch discovery (never hardcodes 'main')
  * Branch selection, creation, comparison, push/commit status & divergence/conflict reporting
  * Safe user file create / update / rename / delete with stale-SHA protection
  * Full Pull Request review, comments, and fail-closed single-use approval-gated merge
  * End-to-end Orchestrator + Dynamic Agent Selection + DAG -> real GitHub branch commits + pytest + PR
  * Mandatory per-project repository isolation (`assert_repo`) on every operation
"""
from __future__ import annotations

import re
import os
import subprocess
import sys
import time
from pathlib import Path

from execution.sandbox import Sandbox
from integrations.github.api import GitHubAPI
from integrations.github.client import GitHubClient
from integrations.github.diff import summarize_compare, summarize_pr_files
from security.approval import ApprovalManager
from security.code_scanner import detect_high_confidence_secrets
from security.secret_manager import redact
from security.validation import validate_branch_name, validate_repo_full_name, validate_repo_path

_PR_REF_RE = re.compile(r"\bPR\s*#(\d+)\b", re.IGNORECASE)


class GitHubExecutionService:
    def __init__(self, db, api: GitHubAPI, approvals: ApprovalManager | None = None):
        self.db = db
        self.api = api
        # Fail-closed: always maintain an ApprovalManager — never skip merge approval checks.
        self.approvals = approvals if approvals is not None else ApprovalManager(db)

    # ---------- repository binding & default-branch resolution ----------
    def _bound_repo(self, project_id: str) -> tuple[dict | None, dict | None]:
        row = self.db.get_repository(project_id)
        if not row:
            return None, {"ok": False, "status": 404, "error": "Connect a GitHub repository to this project first"}
        verified = self.db.assert_repo(project_id, row["repo_full_name"])
        return verified, None

    def connect_repository(self, project_id: str, repo_full_name: str, *,
                           visibility: str | None = None,
                           default_branch: str | None = None,
                           working_branch: str | None = None,
                           source: str = "dashboard") -> dict:
        """Bind a GitHub repository, discovering its real configured default_branch when reachable."""
        clean_repo = (repo_full_name or "").strip()
        ok_name, err_name = validate_repo_full_name(clean_repo)
        if not ok_name:
            return {"ok": False, "status": 400, "error": err_name}

        remote_verified = False
        permissions: dict = {}
        resolved_default = (default_branch or "").strip()
        resolved_vis = (visibility or "").strip()

        if self.api and self.api.token:
            meta = self.api.permissions(clean_repo)
            if meta.get("ok"):
                remote_verified = True
                permissions = meta.get("permissions") or {}
                if meta.get("default_branch"):
                    # GitHub's configured default is authoritative, not a client override.
                    resolved_default = str(meta["default_branch"]).strip()
                if not resolved_vis:
                    resolved_vis = str(meta.get("visibility") or ("private" if meta.get("private") else "public"))
            elif not resolved_default:
                return {
                    "ok": False,
                    "status": meta.get("status") or 502,
                    "error": f"Could not inspect repository {clean_repo}: {meta.get('error')}",
                }

        if not resolved_vis:
            resolved_vis = "private"
        resolved_working = (working_branch or "").strip() or (resolved_default or None)

        if remote_verified and resolved_working and resolved_working != resolved_default:
            sha, sha_res = self.api.branch_sha(clean_repo, resolved_working)
            if not sha:
                return {
                    "ok": False,
                    "status": sha_res.get("status") or 404,
                    "error": f"Working branch {resolved_working!r} not found on {clean_repo}: {sha_res.get('error')}",
                }

        try:
            binding = self.db.bind_repository(
                project_id, clean_repo, resolved_vis, resolved_default, resolved_working,
            )
        except Exception as e:
            return {"ok": False, "status": 400, "error": redact(str(e))}

        self.db.record_sync(
            project_id, source, "repository_connected",
            f"{clean_repo} default={resolved_default or 'unverified'} working={resolved_working or 'unverified'}",
        )
        return {
            "ok": True,
            "status": 200,
            "repository": binding,
            "remote_verified": remote_verified,
            "permissions": permissions,
        }

    def refresh_repository(self, project_id: str, *, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        meta = self.api.permissions(repo["repo_full_name"])
        if not meta.get("ok"):
            return meta
        new_default = (meta.get("default_branch") or repo.get("default_branch") or "").strip()
        cur_working = (repo.get("working_branch") or new_default or "").strip()
        vis = meta.get("visibility") or repo.get("visibility") or "private"
        updated = self.db.bind_repository(project_id, repo["repo_full_name"], vis, new_default, cur_working)
        self.db.record_sync(project_id, source, "repository_refreshed", f"default={new_default} working={cur_working}")
        return {
            "ok": True,
            "status": 200,
            "repository": updated,
            "permissions": meta.get("permissions") or {},
        }

    def resolve_default_branch(self, project_id: str) -> tuple[str | None, dict | None]:
        """Return the repository's configured default branch (fetches live from GitHub if unset)."""
        repo, err = self._bound_repo(project_id)
        if err:
            return None, err
        stored = (repo.get("default_branch") or "").strip()
        if stored:
            return stored, None
        live_default, meta_res = self.api.default_branch(repo["repo_full_name"])
        if not live_default:
            return None, {
                "ok": False,
                "status": meta_res.get("status") or 400,
                "error": (
                    f"Repository default branch is unknown and could not be queried from GitHub: "
                    f"{meta_res.get('error', 'unconfigured')}"
                ),
            }
        working = (repo.get("working_branch") or live_default).strip()
        self.db.bind_repository(
            project_id, repo["repo_full_name"], repo.get("visibility") or "private", live_default, working,
        )
        return live_default, None

    def resolve_working_branch(self, project_id: str, requested_branch: str | None = None) -> tuple[str | None, dict | None]:
        """Resolve effective branch: explicit request -> saved working_branch -> repo default_branch."""
        repo, err = self._bound_repo(project_id)
        if err:
            return None, err
        if requested_branch and requested_branch.strip():
            ok_b, err_b = validate_branch_name(requested_branch)
            if not ok_b:
                return None, {"ok": False, "status": 400, "error": err_b}
            return requested_branch.strip(), None
        saved = (repo.get("working_branch") or "").strip()
        if saved:
            return saved, None
        return self.resolve_default_branch(project_id)

    # ---------- branch management, comparison & status ----------
    def list_branches(self, project_id: str) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        default_br, _ = self.resolve_default_branch(project_id)
        repo = self.db.get_repository(project_id) or repo
        working_br = (repo.get("working_branch") or default_br or "").strip()
        r = self.api.branches(repo["repo_full_name"])
        if not r.get("ok"):
            return r
        enriched = []
        for item in r.get("data") or []:
            if not isinstance(item, dict):
                continue
            bname = item.get("name") or ""
            sha = ((item.get("commit") or {}).get("sha") if isinstance(item.get("commit"), dict) else "") or ""
            enriched.append({
                "name": bname,
                "sha": sha,
                "protected": bool(item.get("protected", False)),
                "is_default": bool(default_br and bname == default_br),
                "is_working": bool(working_br and bname == working_br),
            })
        return {
            "ok": True,
            "status": 200,
            "default_branch": default_br or "",
            "working_branch": working_br,
            "data": enriched,
        }

    def select_working_branch(self, project_id: str, branch_name: str, *,
                              verify_remote: bool = True, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        ok_b, err_b = validate_branch_name(branch_name)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        clean = branch_name.strip()
        sha = ""
        if verify_remote and self.api.token:
            found_sha, sha_res = self.api.branch_sha(repo["repo_full_name"], clean)
            if not found_sha:
                return {
                    "ok": False,
                    "status": sha_res.get("status") or 404,
                    "error": f"Branch {clean!r} not found on {repo['repo_full_name']}: {sha_res.get('error')}",
                }
            sha = found_sha
        updated = self.db.set_working_branch(project_id, clean, source=source)
        return {"ok": True, "status": 200, "repository": updated, "working_branch": clean, "sha": sha}

    def create_branch(self, project_id: str, branch_name: str, *,
                      from_branch: str | None = None,
                      select_working: bool = True,
                      source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        ok_b, err_b = validate_branch_name(branch_name)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        base = (from_branch or "").strip()
        if not base:
            resolved_default, derr = self.resolve_default_branch(project_id)
            if derr:
                return derr
            base = resolved_default or ""
        ok_base, err_base = validate_branch_name(base)
        if not ok_base:
            return {"ok": False, "status": 400, "error": f"Invalid base branch: {err_base}"}
        res = self.api.create_branch(repo["repo_full_name"], branch_name.strip(), base)
        if not res.get("ok"):
            return res
        updated = repo
        if select_working:
            updated = self.db.set_working_branch(project_id, branch_name.strip(), source=source)
        self.db.record_sync(project_id, source, "branch_created", f"{branch_name.strip()} from {base}")
        return {
            "ok": True,
            "status": 201,
            "branch": branch_name.strip(),
            "from_branch": base,
            "sha": res.get("sha") or ((res.get("data") or {}).get("object") or {}).get("sha", ""),
            "repository": updated,
        }

    def compare_branches(self, project_id: str, *, base: str | None = None, head: str | None = None) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        resolved_base = (base or "").strip()
        if not resolved_base:
            resolved_base, berr = self.resolve_default_branch(project_id)
            if berr:
                return berr
        resolved_head, herr = self.resolve_working_branch(project_id, head)
        if herr:
            return herr
        r = self.api.compare(repo["repo_full_name"], resolved_base or "", resolved_head or "")
        if not r.get("ok"):
            return {
                "ok": False,
                "status": r.get("status") or 502,
                "base": resolved_base,
                "head": resolved_head,
                "diff": None,
                "error": r.get("error"),
            }
        summary = summarize_compare(r.get("data") or {}, base=resolved_base or "", head=resolved_head or "")
        return {
            "ok": True,
            "status": 200,
            "default_branch": repo.get("default_branch") or resolved_base,
            "working_branch": repo.get("working_branch") or resolved_head,
            "base": resolved_base,
            "head": resolved_head,
            "diff": summary,
        }

    def branch_status(self, project_id: str, *, branch: str | None = None, base: str | None = None) -> dict:
        """Report remote commit/push status, ahead/behind counts, divergence/conflicts, and local git state."""
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        default_br, derr = self.resolve_default_branch(project_id)
        if derr:
            return derr
        resolved_base = (base or "").strip() or (default_br or "")
        resolved_head, herr = self.resolve_working_branch(project_id, branch)
        if herr:
            return herr

        head_sha, head_res = self.api.branch_sha(repo["repo_full_name"], resolved_head or "")
        if not head_sha:
            return head_res
        base_sha, _ = self.api.branch_sha(repo["repo_full_name"], resolved_base)

        ahead, behind, status_label, total_commits, changed_files, diverged = 0, 0, "identical", 0, 0, False
        compare_error = ""
        if resolved_head != resolved_base:
            cmp = self.api.compare(repo["repo_full_name"], resolved_base, resolved_head or "")
            if cmp.get("ok"):
                summary = summarize_compare(cmp.get("data") or {}, base=resolved_base, head=resolved_head or "")
                ahead = summary["ahead_by"]
                behind = summary["behind_by"]
                status_label = summary["status"]
                total_commits = summary["total_commits"]
                changed_files = len(summary["files"])
                diverged = summary["diverged"]
            else:
                compare_error = cmp.get("error") or ""

        commits_res = self.api.commits(repo["repo_full_name"], resolved_head, per_page=10)
        recent_commits = []
        if commits_res.get("ok") and isinstance(commits_res.get("data"), list):
            for c in commits_res["data"][:10]:
                cobj = c.get("commit") or {}
                recent_commits.append({
                    "sha": (c.get("sha") or "")[:7],
                    "full_sha": c.get("sha") or "",
                    "message": ((cobj.get("message") or "").splitlines() or [""])[0],
                    "author": ((cobj.get("author") or {}).get("name")) or "",
                    "date": ((cobj.get("author") or {}).get("date")) or "",
                })

        local_status = None
        proj = self.db.get_project(project_id)
        if proj and proj.get("workspace") and Path(proj["workspace"]).is_dir():
            sb = Sandbox(proj["workspace"])
            if sb.is_repo():
                gs = GitHubClient(repo=repo["repo_full_name"], token=self.api.token, approval_manager=self.approvals).status(proj["workspace"])
                if gs.get("ok"):
                    local_status = gs

        conflict_warning = ""
        if diverged:
            conflict_warning = (
                f"Branch {resolved_head!r} has diverged from {resolved_base!r} "
                f"(+{ahead} ahead, -{behind} behind). Review diff or rebase/merge before merging PR."
            )
        elif local_status and local_status.get("conflicts"):
            conflict_warning = f"Local workspace has unresolved merge conflicts in: {', '.join(local_status['conflicts'])}"

        return {
            "ok": True,
            "status": 200,
            "repo": repo["repo_full_name"],
            "default_branch": default_br,
            "working_branch": repo.get("working_branch") or resolved_head,
            "branch": resolved_head,
            "base": resolved_base,
            "head_sha": head_sha,
            "base_sha": base_sha or "",
            "compare_status": status_label,
            "ahead_by": ahead,
            "behind_by": behind,
            "total_commits": total_commits,
            "changed_files_count": changed_files,
            "diverged": diverged,
            "has_conflicts": bool(diverged or (local_status and local_status.get("conflicts"))),
            "conflict_warning": conflict_warning,
            "compare_error": compare_error,
            "push_state": (
                "synced_on_default" if resolved_head == default_br
                else "ready_for_pr" if (ahead > 0 and not diverged)
                else "diverged_from_base" if diverged
                else "behind_base" if behind > 0
                else "up_to_date"
            ),
            "recent_commits": recent_commits,
            "local_workspace": local_status,
        }

    def prepare_branch(self, project_id: str, task_id: str, *,
                       base_branch: str | None = None,
                       branch_name: str | None = None,
                       use_existing: bool = False) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        default_br, derr = self.resolve_default_branch(project_id)
        if derr:
            return derr
        base = (base_branch or "").strip() or (default_br or "")
        clean_tid = re.sub(r"[^A-Za-z0-9._\-]", "", task_id.replace("task_", ""))[:32] or "run"
        clean_pid = re.sub(r"[^A-Za-z0-9._\-]", "", project_id.replace("proj_", ""))[:24] or "proj"
        target = (branch_name or "").strip() or f"agent/{clean_pid}/{clean_tid}"
        ok_t, err_t = validate_branch_name(target)
        if not ok_t:
            return {"ok": False, "status": 400, "error": err_t}

        if use_existing:
            if target == default_br:
                return {
                    "ok": False,
                    "status": 403,
                    "error": f"Refusing automated agent writes directly to default branch {default_br!r}; use a working branch",
                }
            sha, sha_res = self.api.branch_sha(repo["repo_full_name"], target)
            if not sha:
                return {"ok": False, "status": sha_res.get("status") or 404, "branch": target,
                        "repo": repo["repo_full_name"], "error": sha_res.get("error"), "api": sha_res}
            return {
                "ok": True,
                "branch": target,
                "base_branch": base,
                "default_branch": default_br,
                "repo": repo["repo_full_name"],
                "sha": sha,
                "reused": True,
                "api": sha_res,
            }

        r = self.api.create_branch(repo["repo_full_name"], target, base)
        return {
            "ok": bool(r.get("ok", False)),
            "branch": target,
            "base_branch": base,
            "default_branch": default_br,
            "repo": repo["repo_full_name"],
            "conflict": bool(r.get("conflict", False)),
            "error": r.get("error"),
            "api": r,
        }

    # ---------- user file browser operations (stale-SHA safe) ----------
    def list_files(self, project_id: str, path: str = "", branch: str | None = None) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        r = self.api.list_dir(repo["repo_full_name"], path or "", target_br)
        if r.get("ok"):
            r["default_branch"] = repo.get("default_branch") or ""
            r["working_branch"] = repo.get("working_branch") or target_br
        return r

    def read_user_file(self, project_id: str, path: str, branch: str | None = None) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        return self.api.read_text(repo["repo_full_name"], path, target_br)

    def create_user_file(self, project_id: str, branch: str | None, path: str,
                         content: str, message: str | None = None, *, source: str = "dashboard") -> dict:
        """Create a new file on `branch`; fails with 409 conflict if path already exists."""
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        r = self.api.create_file(
            repo["repo_full_name"], path, content, message or f"Create {path}", target_br or "",
        )
        if r.get("ok"):
            self.db.record_sync(project_id, source, "file_created", f"{target_br}:{r.get('path', path)}")
        return r

    def save_user_file(self, project_id: str, branch: str | None, path: str, content: str,
                       sha: str | None = None, message: str | None = None, *,
                       allow_create: bool = False, source: str = "dashboard") -> dict:
        """User edit from Android: enforces stale-SHA conflict detection and never overwrites silently."""
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        if sha and sha.strip():
            r = self.api.update_file(
                repo["repo_full_name"], path, content, message or f"Update {path}", target_br or "", sha.strip(),
            )
            if r.get("ok"):
                self.db.record_sync(project_id, source, "file_updated", f"{target_br}:{r.get('path', path)}")
            return r
        if allow_create:
            return self.create_user_file(project_id, target_br, path, content, message, source=source)
        # No SHA supplied: check whether file already exists. If it exists, block blind overwrite!
        existing = self.api.get_file(repo["repo_full_name"], path, target_br)
        if existing.get("ok"):
            cur_sha = (existing.get("data") or {}).get("sha", "") if isinstance(existing.get("data"), dict) else ""
            return {
                "ok": False,
                "status": 409,
                "conflict": True,
                "current_sha": cur_sha,
                "error": (
                    f"Refusing to overwrite existing file {path!r} without its current SHA. "
                    "Open or refresh the file before saving."
                ),
            }
        if existing.get("status") == 404:
            r = self.api.put_file(
                repo["repo_full_name"], path, content, message or f"Create {path}", target_br or "", sha=None,
            )
            if r.get("ok"):
                self.db.record_sync(project_id, source, "file_created", f"{target_br}:{r.get('path', path)}")
            return r
        return {
            "ok": False,
            "status": existing.get("status") or 409,
            "conflict": True,
            "error": f"Cannot verify remote file state before save: {existing.get('error', 'missing SHA')}",
        }

    def rename_user_file(self, project_id: str, branch: str | None, old_path: str, new_path: str,
                         sha: str | None = None, message: str | None = None, *,
                         source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        r = self.api.rename_file(
            repo["repo_full_name"], old_path, new_path, target_br or "", sha=sha, message=message,
        )
        if r.get("ok"):
            self.db.record_sync(project_id, source, "file_renamed", f"{target_br}:{old_path}->{new_path}")
        return r

    def delete_user_file(self, project_id: str, branch: str | None, path: str, sha: str,
                         message: str | None = None, *, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        target_br, berr = self.resolve_working_branch(project_id, branch)
        if berr:
            return berr
        clean_sha = (sha or "").strip()
        if not clean_sha:
            return {"ok": False, "status": 400, "error": "File SHA is required to delete a file safely"}
        existing = self.api.get_file(repo["repo_full_name"], path, target_br)
        if existing.get("ok") and isinstance(existing.get("data"), dict):
            remote_sha = (existing["data"].get("sha") or "").strip()
            if remote_sha and remote_sha != clean_sha:
                return {
                    "ok": False,
                    "status": 409,
                    "conflict": True,
                    "current_sha": remote_sha,
                    "error": f"Stale SHA conflict: {path} changed remotely ({remote_sha[:7]}) — refresh before deleting.",
                }
        r = self.api.delete_file(
            repo["repo_full_name"], path, message or f"Delete {path}", target_br or "", clean_sha,
        )
        if r.get("ok"):
            self.db.record_sync(project_id, source, "file_deleted", f"{target_br}:{path}")
        return r

    # ---------- agent file commits ----------
    def commit_file(self, project_id: str, task_id: str, agent_id: str,
                    branch: str, path: str, content: str) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        existing = self.api.get_file(repo["repo_full_name"], path, branch)
        data = existing.get("data") if existing.get("ok") and isinstance(existing.get("data"), dict) else {}
        sha = data.get("sha") if isinstance(data, dict) else None
        return self.api.put_file(
            repo["repo_full_name"], path, content, f"{agent_id}: {task_id} {path}", branch, sha,
        )

    def execute_agent_files(self, project_id: str, task_id: str, agent_id: str,
                            branch: str, files: dict[str, str], *,
                            base_branch: str | None = None) -> dict:
        """Commit real agent file outputs, then verify via GitHub compare that all files landed."""
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        if not files:
            return {"ok": False, "status": 400, "error": "No agent files supplied to commit"}
        base = (base_branch or "").strip()
        if not base:
            resolved_default, derr = self.resolve_default_branch(project_id)
            if derr:
                return derr
            base = resolved_default or ""
        results: dict[str, dict] = {}
        commit_shas: list[str] = []
        for path, content in files.items():
            r = self.commit_file(project_id, task_id, agent_id, branch, path, content)
            results[path] = r
            if not r.get("ok"):
                return {"ok": False, "error": f"commit failed for {path}: {r.get('error')}", "results": results}
            if r.get("commit_sha"):
                commit_shas.append(r["commit_sha"])
        cmp = self.api.compare(repo["repo_full_name"], base, branch)
        changed = [f.get("filename") for f in (cmp.get("data") or {}).get("files", [])] if cmp.get("ok") else []
        verified = set(files.keys()) <= set(changed) if cmp.get("ok") else False
        self.db.record_sync(
            project_id, agent_id, "agent_files_committed",
            f"{branch}: {len(files)} file(s), verified={verified}",
        )
        return {
            "ok": True,
            "verified": verified,
            "branch": branch,
            "base_branch": base,
            "changed_files": changed,
            "commit_shas": commit_shas,
            "results": results,
        }

    # ---------- Pull Request lifecycle & approval-gated merge ----------
    def list_pull_requests(self, project_id: str, state: str = "all") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        r = self.api.list_prs(repo["repo_full_name"], state=state)
        if not r.get("ok"):
            return r
        prs = []
        for item in r.get("data") or []:
            if not isinstance(item, dict):
                continue
            merged = bool(item.get("merged_at"))
            raw_state = (item.get("state") or "open").lower()
            display_state = "merged" if merged else raw_state
            head = item.get("head") or {}
            base = item.get("base") or {}
            prs.append({
                "number": item.get("number"),
                "title": item.get("title") or "",
                "state": raw_state,
                "display_state": display_state,
                "merged": merged,
                "draft": bool(item.get("draft", False)),
                "head_branch": head.get("ref") or "",
                "head_sha": head.get("sha") or "",
                "base_branch": base.get("ref") or "",
                "author": (item.get("user") or {}).get("login") or "",
                "html_url": item.get("html_url") or "",
                "created_at": item.get("created_at") or "",
                "updated_at": item.get("updated_at") or "",
            })
        return {
            "ok": True,
            "status": 200,
            "repo": repo["repo_full_name"],
            "default_branch": repo.get("default_branch") or "",
            "data": prs,
            "recorded": self.db.list_pull_requests(project_id),
        }

    def open_pull_request(self, project_id: str, task_id: str, branch: str, title: str,
                          agent_summary: str = "", test_results: str = "", *,
                          base_branch: str | None = None, body: str | None = None,
                          draft: bool = False, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        resolved_base = (base_branch or "").strip()
        if not resolved_base:
            resolved_base, berr = self.resolve_default_branch(project_id)
            if berr:
                return berr
        head = (branch or "").strip()
        if not head:
            resolved_head, herr = self.resolve_working_branch(project_id)
            if herr:
                return herr
            head = resolved_head or ""
        pr_body = body if (body is not None and body.strip()) else (
            f"Task: {task_id or 'manual'}\n\n"
            f"Agents: {agent_summary or 'N/A'}\n\n"
            f"Tests: {test_results or 'N/A'}\n\n"
            "Generated by Phone AI Agent Team."
        )
        r = self.api.create_pr(
            repo["repo_full_name"], title, head, resolved_base, pr_body, draft=draft,
        )
        if r.get("ok"):
            d = r.get("data") or {}
            num = d.get("number")
            url = d.get("html_url") or ""
            self.db.record_pull_request(
                project_id, repo["repo_full_name"], num, title, head,
                resolved_base or "", url, task_id or None, state="OPEN",
            )
            self.db.record_sync(project_id, source, "pr_opened", f"#{num} {title} ({head}->{resolved_base})")
        return r

    def _pr_approvals(self, project_id: str, number: int) -> list[dict]:
        rows = self.db.list_approvals(project_id=project_id, limit=50)
        tag = f"PR #{int(number)}"
        out = []
        for row in rows:
            if row.get("action") != "pr_merge":
                continue
            detail = row.get("detail") or ""
            m = _PR_REF_RE.search(detail)
            if m and int(m.group(1)) == int(number):
                out.append(row)
            elif tag.lower() in detail.lower():
                out.append(row)
        return out

    def get_pull_request_detail(self, project_id: str, number) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        pr_res = self.api.get_pr(repo["repo_full_name"], number)
        if not pr_res.get("ok"):
            return pr_res
        d = pr_res.get("data") or {}
        pr_num = int(d.get("number") or number)
        reviews_res = self.api.pr_reviews(repo["repo_full_name"], pr_num)
        comments_res = self.api.pr_comments(repo["repo_full_name"], pr_num)
        approvals = self._pr_approvals(project_id, pr_num)

        reviews = []
        if reviews_res.get("ok") and isinstance(reviews_res.get("data"), list):
            for rv in reviews_res["data"]:
                if not isinstance(rv, dict):
                    continue
                reviews.append({
                    "id": rv.get("id"),
                    "user": (rv.get("user") or {}).get("login") or "",
                    "state": rv.get("state") or "",
                    "body": rv.get("body") or "",
                    "submitted_at": rv.get("submitted_at") or "",
                })
        comments = comments_res.get("data", []) if comments_res.get("ok") else []

        merged = bool(d.get("merged") or d.get("merged_at"))
        state = (d.get("state") or "open").lower()
        mergeable = d.get("mergeable")
        mergeable_state = d.get("mergeable_state") or ""
        ready_approval = next(
            (a for a in approvals if a["status"] == "APPROVED" and a.get("consumed_at") is None
             and (a.get("expires_at") is None or a["expires_at"] > time.time())
             and (a.get("detail") or "") == f"Merge PR #{pr_num} on {repo['repo_full_name']} head={str((d.get('head') or {}).get('sha') or '').lower()}"),
            None,
        )
        pending_approval = next((a for a in approvals if a["status"] == "PENDING"), None)

        if merged:
            readiness = "merged"
            self.db.update_pull_request_state(
                project_id, pr_num, "MERGED", merge_commit_sha=d.get("merge_commit_sha"),
            )
        elif state == "closed":
            readiness = "closed"
            self.db.update_pull_request_state(project_id, pr_num, "CLOSED")
        elif mergeable is False or mergeable_state == "dirty":
            readiness = "conflicting"
        elif ready_approval:
            readiness = "authorized_to_merge"
        elif pending_approval:
            readiness = "pending_authorization"
        else:
            readiness = "authorization_required"

        head = d.get("head") or {}
        base = d.get("base") or {}
        return {
            "ok": True,
            "status": 200,
            "pr": {
                "number": pr_num,
                "title": d.get("title") or "",
                "body": d.get("body") or "",
                "state": state,
                "display_state": "merged" if merged else state,
                "merged": merged,
                "merged_at": d.get("merged_at"),
                "merge_commit_sha": d.get("merge_commit_sha") or "",
                "mergeable": mergeable,
                "mergeable_state": mergeable_state,
                "merge_readiness": readiness,
                "draft": bool(d.get("draft", False)),
                "head_branch": head.get("ref") or "",
                "head_sha": head.get("sha") or "",
                "base_branch": base.get("ref") or "",
                "base_sha": base.get("sha") or "",
                "author": (d.get("user") or {}).get("login") or "",
                "additions": int(d.get("additions") or 0),
                "deletions": int(d.get("deletions") or 0),
                "changed_files": int(d.get("changed_files") or 0),
                "commits": int(d.get("commits") or 0),
                "html_url": d.get("html_url") or "",
                "created_at": d.get("created_at") or "",
                "updated_at": d.get("updated_at") or "",
            },
            "reviews": reviews,
            "comments": comments,
            "approvals": approvals,
            "ready_approval_id": ready_approval["id"] if ready_approval else None,
            "pending_approval_id": pending_approval["id"] if pending_approval else None,
        }

    def list_pull_request_files(self, project_id: str, number) -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        r = self.api.pr_files(repo["repo_full_name"], number)
        if not r.get("ok"):
            return r
        files = summarize_pr_files(r.get("data") or [])
        total_add = sum(f["additions"] for f in files)
        total_del = sum(f["deletions"] for f in files)
        return {
            "ok": True,
            "status": 200,
            "number": int(number),
            "total_files": len(files),
            "additions": total_add,
            "deletions": total_del,
            "files": files,
        }

    def comment_pull_request(self, project_id: str, number, body: str, *,
                             path: str | None = None, line: int | None = None,
                             commit_id: str | None = None, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        r = self.api.add_pr_comment(
            repo["repo_full_name"], number, body, path=path, line=line, commit_id=commit_id,
        )
        if r.get("ok"):
            loc = f" ({path}:{line})" if (path and line) else ""
            self.db.record_sync(project_id, source, "pr_commented", f"#{number}{loc}")
        return r

    def review_pull_request(self, project_id: str, number, event: str, body: str = "",
                            comments: list[dict] | None = None, *, source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        ev = (event or "COMMENT").strip().upper()
        r = self.api.review_pr(repo["repo_full_name"], number, event=ev, body=body, comments=comments)
        if r.get("ok"):
            self.db.record_sync(project_id, source, f"pr_review_{ev.lower()}", f"#{number}")
        return r

    def request_merge_approval(self, project_id: str, number, *,
                               title: str = "", head: str = "", base: str = "",
                               source: str = "dashboard") -> dict:
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        try:
            pr_num = int(str(number).strip())
            if pr_num <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return {"ok": False, "status": 400, "error": f"Invalid PR number: {number!r}"}

        # Bind the authorization to the actual current GitHub head, not caller-supplied metadata.
        pr = self.api.get_pr(repo["repo_full_name"], pr_num)
        if not pr.get("ok"):
            return pr
        data = pr.get("data") or {}
        head_sha = (data.get("head") or {}).get("sha") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
            return {"ok": False, "status": 502, "error": "GitHub did not provide a valid PR head SHA"}
        scope = f"Merge PR #{pr_num} on {repo['repo_full_name']} head={head_sha.lower()}"
        for existing in self._pr_approvals(project_id, pr_num):
            if existing["status"] == "PENDING" and (existing.get("detail") or "") == scope:
                return {"ok": True, "status": 200, "id": existing["id"], "reused": True, "approval": existing}

        detail = scope
        aid = self.approvals.require(project_id, "pr_merge", detail)
        self.db.record_sync(project_id, source, "pr_merge_approval_requested", f"#{pr_num} approval={aid}")
        return {"ok": True, "status": 201, "id": aid, "reused": False, "approval": self.approvals.get(aid)}

    def merge(self, project_id: str, number, approval_id: str | None, *,
              merge_method: str = "merge", commit_title: str | None = None,
              commit_message: str | None = None, sha: str | None = None,
              actor: str = "dashboard") -> dict:
        """Fail-closed PR merge: strictly verifies and consumes user approval before merging."""
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        try:
            pr_num = int(str(number).strip())
            if pr_num <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return {"ok": False, "status": 400, "error": f"Invalid PR number: {number!r}"}

        method = (merge_method or "merge").strip().lower()
        if method not in {"merge", "squash", "rebase"}:
            return {"ok": False, "status": 400, "error": f"Invalid merge_method: {merge_method!r}"}

        if not approval_id or not str(approval_id).strip():
            return {
                "ok": False,
                "status": 403,
                "needs_approval": True,
                "error": "Merge blocked: explicit pr_merge approval_id is required",
            }
        clean_aid = str(approval_id).strip()
        row = self.approvals.get(clean_aid)
        if not row:
            self.approvals.verify(clean_aid, project_id=project_id, action="pr_merge", actor=actor)
            return {"ok": False, "status": 403, "error": f"Merge blocked: unknown approval_id {clean_aid}"}

        # Approvals must explicitly name both this PR and its bound repository.
        # A generic git_push or an approval for a previous repository is insufficient.
        detail = row.get("detail") or ""
        scope = f"Merge PR #{pr_num} on {repo['repo_full_name']} head="
        authorized_sha = detail[len(scope):] if detail.startswith(scope) else ""
        if row.get("action") != "pr_merge" or not re.fullmatch(r"[0-9a-f]{40}", authorized_sha):
            self.db.log_approval_audit(
                clean_aid, project_id, "VERIFY_FAILED_PR_MISMATCH",
                actor=actor, detail=f"expected={scope}<head SHA>",
            )
            return {"ok": False, "status": 403, "error": "Merge blocked: approval does not authorize this PR, repository and head SHA"}
        if sha and sha.lower() != authorized_sha:
            return {"ok": False, "status": 409, "conflict": True, "error": "PR head differs from the approved SHA; request a new approval"}

        ok, why = self.approvals.verify(
            clean_aid, project_id=project_id, action="pr_merge", consume=True, actor=actor,
        )
        if not ok:
            return {"ok": False, "status": 403, "needs_approval": True, "error": f"Merge blocked: {why}"}

        res = self.api.merge_pr(
            repo["repo_full_name"], pr_num,
            merge_method=method, commit_title=commit_title, commit_message=commit_message, sha=authorized_sha,
        )
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        merged = bool(res.get("ok") and data.get("merged", True))
        merge_sha = data.get("sha") or ""
        message = data.get("message") or (
            "Pull Request merged successfully" if merged else (res.get("error") or "Merge failed")
        )

        if merged:
            self.db.update_pull_request_state(
                project_id, pr_num, "MERGED", merge_commit_sha=merge_sha, approval_id=clean_aid,
            )
            self.db.record_sync(
                project_id, actor, "pr_merged",
                f"#{pr_num} method={method} sha={merge_sha[:12]} approval={clean_aid}",
            )
            return {
                "ok": True,
                "status": 200,
                "number": pr_num,
                "merged": True,
                "merge_method": method,
                "sha": merge_sha,
                "message": redact(message),
                "approval_id": clean_aid,
                "approval_consumed": True,
                "conflict": False,
            }

        self.db.record_sync(
            project_id, actor, "pr_merge_failed", f"#{pr_num}: {redact(message)[:160]}",
        )
        return {
            "ok": False,
            "status": res.get("status") or 409,
            "number": pr_num,
            "merged": False,
            "merge_method": method,
            "sha": "",
            "message": redact(message),
            "error": redact(res.get("error") or message),
            "approval_id": clean_aid,
            "approval_consumed": True,
            "conflict": bool(res.get("conflict", False)),
        }

    # ---------- full end-to-end Orchestrator -> GitHub Agent Execution ----------
    def execute_project_on_github(
        self,
        engine,
        project_id: str,
        instruction: str,
        *,
        branch_name: str | None = None,
        use_existing_branch: bool = False,
        base_branch: str | None = None,
        target_prefix: str = "",
        open_pr: bool = True,
        pr_title: str | None = None,
        quick: bool = False,
        dynamic: bool = True,
        timeout_seconds: int = 180,
        source: str = "dashboard",
    ) -> dict:
        """Run the full 10-step Orchestrator + DAG + pytest + GitHub commit + PR workflow.

        1. Records user instruction for `project_id`
        2. Analyzes blueprint via BlueprintAnalyzer
        3. Dynamically selects required agents & records excluded agents
        4. Executes tasks in strict DAG dependency order via OrchestratorEngine
        5. Collects real agent-generated source, test, and documentation files
        6. Scans all generated files for secrets, then commits them to the target GitHub branch
        7. Verifies commits landed on GitHub via `commits` + `compare`
        8. Runs real pytest against generated tests in the project workspace
        9. Opens a Pull Request against the repository's configured default branch (when `open_pr=True`)
        10. Persists PR + synchronization metadata so user can review, comment, approve & merge
        """
        repo, err = self._bound_repo(project_id)
        if err:
            return err
        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "status": 404, "error": "Project not found"}
        clean_instruction = (instruction or "").strip()
        if not clean_instruction:
            return {"ok": False, "status": 400, "error": "Development instruction is required"}

        # This pipeline does not yet import a bounded repository snapshot or run in
        # an isolated worker. Refuse before starting agents or writing remote files.
        from execution.isolation import isolated_worker_available
        if not isolated_worker_available():
            return {"ok": False, "status": 503, "blocked": True,
                    "error": "GitHub agent execution is unavailable until repository-context handling and isolated workers are implemented"}

        prefix = (target_prefix or "").strip().strip("/")
        if prefix:
            ok_pref, pref_err = validate_repo_path(prefix, allow_empty=False)
            if not ok_pref:
                return {"ok": False, "status": 400, "error": f"Invalid target_prefix: {pref_err}"}

        self.db.record_sync(project_id, source, "github_execution_started", clean_instruction[:200])

        # Steps 1-4: Plan and run via OrchestratorEngine
        existing_tasks = self.db.list_tasks(project_id)
        if not existing_tasks:
            task_ids = engine.start_project(
                project_id, requirements=clean_instruction, quick=quick, dynamic=dynamic,
            )
        else:
            replan_res = engine.replan_project(project_id, additional_requirements=clean_instruction)
            if not replan_res.get("ok"):
                return replan_res
            task_ids = [t["id"] for t in self.db.list_tasks(project_id)]

        deadline = time.time() + max(15, int(timeout_seconds))
        while time.time() < deadline:
            lc = engine.lifecycle.get(project_id)
            if lc in ("COMPLETED", "FAILED", "CANCELLED", "WAITING_FOR_APPROVAL"):
                break
            time.sleep(0.2)

        st = engine.status(project_id)
        if st["lifecycle"] != "COMPLETED":
            return {
                "ok": False,
                "status": 500,
                "lifecycle": st["lifecycle"],
                "tasks": st["tasks"],
                "error": f"Orchestrator execution ended in state {st['lifecycle']}",
            }

        analysis = st.get("blueprint_analysis") or {}
        selected_agents = analysis.get("selected_agents") or [t["agent"] for t in st["tasks"]]
        excluded_agents = analysis.get("excluded_agents") or {}

        # Verify DAG dependency ordering from execution timestamps
        execs = self.db.list_executions(project_id)
        finish_by_task = {e["task_id"]: e.get("finished_at") or 0 for e in execs if e["status"] == "COMPLETED"}
        start_by_task = {e["task_id"]: e.get("started_at") or 0 for e in execs if e["status"] == "COMPLETED"}
        dag_respected = True
        for t in self.db.list_tasks(project_id):
            t_start = start_by_task.get(t["id"], 0)
            for dep_id in self.db.task_dependencies(t["id"]):
                dep_end = finish_by_task.get(dep_id, 0)
                if dep_end and t_start and t_start + 0.001 < dep_end:
                    dag_respected = False

        # Step 5: Collect generated source/test/doc files & enforce secret scan
        ws = Path(proj["workspace"])
        sb = Sandbox(str(ws))
        secret_findings = sb.scan_secrets()
        if secret_findings:
            return {
                "ok": False,
                "status": 422,
                "blocked_by_secrets": True,
                "findings": secret_findings,
                "error": f"Blocked GitHub push: {len(secret_findings)} secret finding(s) in generated workspace",
            }

        skip_names = {"requirements.txt", ".gitignore"}
        skip_dirs = {".git", "__pycache__", ".pytest_cache", "uploads", "extracted"}
        generated_files: dict[str, str] = {}
        for f in sorted(ws.rglob("*")):
            if not f.is_file() or f.is_symlink():
                continue
            rel_parts = f.relative_to(ws).parts
            if any(part in skip_dirs or part.startswith(".") for part in rel_parts):
                continue
            rel = "/".join(rel_parts)
            if rel in skip_names or rel.endswith(".zip"):
                continue
            if f.stat().st_size > 256 * 1024:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if detect_high_confidence_secrets(text):
                return {
                    "ok": False,
                    "status": 422,
                    "blocked_by_secrets": True,
                    "error": f"Secret detected in generated file {rel}",
                }
            dest_path = f"{prefix}/{rel}" if prefix else rel
            generated_files[dest_path] = text

        if not generated_files:
            return {"ok": False, "status": 500, "error": "Agents produced no committable UTF-8 source files"}

        # Step 8 (verify tests pass before publishing to GitHub & record test results)
        test_summary = self._run_workspace_pytest(ws)

        if not test_summary.get("executed") or not test_summary.get("passed"):
            return {"ok": False, "status": 422, "tests": test_summary,
                    "error": "Generated tests did not execute and pass; no files were published"}

        # Steps 6-7: Prepare target branch & commit generated files to GitHub
        anchor_task = task_ids[0] if task_ids else f"task_{int(time.time())}"
        prep = self.prepare_branch(
            project_id, anchor_task,
            base_branch=base_branch, branch_name=branch_name, use_existing=use_existing_branch,
        )
        if not prep.get("ok"):
            return prep
        target_branch = prep["branch"]
        resolved_base = prep["base_branch"]

        commit_res = self.execute_agent_files(
            project_id, anchor_task, "orchestrator_team",
            target_branch, generated_files, base_branch=resolved_base,
        )
        if not commit_res.get("ok"):
            return commit_res
        if not commit_res.get("verified"):
            return {"ok": False, "status": 502, "branch": target_branch,
                    "committed_files": list(generated_files), "tests": test_summary,
                    "error": "Writes were attempted but GitHub comparison did not verify all files; no PR was opened"}

        self.db.set_working_branch(project_id, target_branch, source=source)

        # Step 9: Open Pull Request (when requested)
        pr_info = None
        if open_pr:
            title = (pr_title or "").strip() or f"feat: {analysis.get('project_title') or proj['name']}"
            agent_list = ", ".join(selected_agents)
            excl_list = ", ".join(excluded_agents.keys()) or "none"
            pr_body = (
                f"## Automated Multi-Agent Delivery\n\n"
                f"- **Project**: `{project_id}` ({proj['name']})\n"
                f"- **Category**: `{analysis.get('software_type', 'unknown')}` "
                f"(complexity: `{analysis.get('complexity', 'unknown')}`)\n"
                f"- **AI Router Mode**: `{engine.router.mode()}`\n"
                f"- **Selected Agents ({len(selected_agents)})**: {agent_list}\n"
                f"- **Excluded Agents ({len(excluded_agents)})**: {excl_list}\n"
                f"- **DAG Dependencies Respected**: `{dag_respected}`\n"
                f"- **Files Committed ({len(generated_files)})**: {', '.join(list(generated_files)[:12])}\n"
                f"- **Test Verification**: {test_summary['summary']}\n"
            )
            pr_res = self.open_pull_request(
                project_id, anchor_task, target_branch, title,
                agent_summary=agent_list, test_results=test_summary["summary"],
                base_branch=resolved_base, body=pr_body, source=source,
            )
            if not pr_res.get("ok"):
                return {
                    "ok": False,
                    "status": pr_res.get("status") or 502,
                    "branch": target_branch,
                    "committed_files": list(generated_files.keys()),
                    "commit_shas": commit_res.get("commit_shas", []),
                    "tests": test_summary,
                    "error": f"Files committed to {target_branch}, but opening PR failed: {pr_res.get('error')}",
                }
            d = pr_res.get("data") or {}
            pr_info = {
                "number": d.get("number"),
                "html_url": d.get("html_url") or "",
                "title": title,
                "head": target_branch,
                "base": resolved_base,
            }

        self.db.record_sync(
            project_id, source, "github_execution_completed",
            f"branch={target_branch} files={len(generated_files)} pr={pr_info['number'] if pr_info else 'none'}",
        )
        return {
            "ok": True,
            "status": 200,
            "project_id": project_id,
            "repo": repo["repo_full_name"],
            "ai_mode": engine.router.mode(),
            "software_type": analysis.get("software_type", ""),
            "selected_agents": selected_agents,
            "excluded_agents": excluded_agents,
            "dag_respected": dag_respected,
            "branch": target_branch,
            "base_branch": resolved_base,
            "committed_files": list(generated_files.keys()),
            "commit_shas": commit_res.get("commit_shas", []),
            "verified_on_github": commit_res.get("verified", False),
            "tests": test_summary,
            "pull_request": pr_info,
        }

    @staticmethod
    def _run_workspace_pytest(workspace: Path) -> dict:
        # pytest executes arbitrary project code. Environment scrubbing is not a sandbox.
        # Require an operator to opt in only in a disposable, isolated environment.
        from execution.isolation import isolated_worker_available, BLOCKED_MESSAGE
        if not isolated_worker_available():
            return {"executed": False, "passed": False, "returncode": None,
                    "summary": BLOCKED_MESSAGE, "output": ""}
        tests_dir = workspace / "tests"
        if not tests_dir.is_dir() or not list(tests_dir.glob("test_*.py")):
            return {"executed": False, "passed": False, "returncode": None, "summary": "No test_*.py files found", "output": ""}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests", "-q"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=60,
                env={"PATH": os.defpath, "HOME": str(workspace), "LANG": "C.UTF-8", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            tail = (out.splitlines()[-1] if out.splitlines() else f"exit={proc.returncode}")
            return {
                "executed": True,
                "passed": proc.returncode == 0,
                "returncode": proc.returncode,
                "summary": redact(tail)[:240],
                "output": redact(out)[:2000],
            }
        except Exception as e:
            return {"executed": False, "passed": False, "returncode": -1, "summary": redact(str(e))[:240], "output": ""}

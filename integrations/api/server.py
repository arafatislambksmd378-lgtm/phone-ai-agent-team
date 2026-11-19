"""Shared JSON REST API for the Android dashboard — shares Database & OrchestratorEngine with Telegram.

Security guarantees (fail-closed, stdlib only):
  * Refuses to instantiate or bind without a strong API_AUTH_TOKEN (>= 24 chars, high entropy).
  * Authentication is NEVER silently disabled on any interface (0.0.0.0, LAN, or 127.0.0.1).
  * Constant-time Bearer token comparison (`hmac.compare_digest`) on every route and HTTP method.
  * Sliding-window per-client brute-force rate limiter on failed authentication attempts.
  * Bounded `Content-Length` enforcement (413 Payload Too Large) & strict JSON validation (400).
  * Optional native TLS 1.2+ (`tls_cert` + `tls_key`) and hardened security response headers.
  * Secret redaction on every error response and log entry; GitHub tokens never leave the backend.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import ssl
import tempfile
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agents.registry import AGENT_NAMES
from execution.file_manager import FileManager
from integrations.github.api import GitHubAPI
from integrations.github.executor import GitHubExecutionService
from security.approval import ApprovalManager
from security.secret_manager import redact
from security.validation import (
    MIN_API_TOKEN_LENGTH,
    is_loopback_host,
    validate_api_token,
    validate_branch_name,
    validate_repo_full_name,
)

log = logging.getLogger("api.server")

API_VERSION = "3.0.0"
DEFAULT_MAX_JSON_BYTES = 2 * 1024 * 1024  # 2 MB for standard JSON requests


class AuthRateLimiter:
    """Thread-safe sliding-window failed-auth limiter per client IP."""

    def __init__(self, max_failures: int = 20, window_seconds: float = 60.0):
        self.max_failures = max(3, int(max_failures))
        self.window_seconds = max(5.0, float(window_seconds))
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, q: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()

    def is_blocked(self, client_ip: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._failures.get(client_ip)
            if not q:
                return False
            self._prune(q, now)
            if not q:
                self._failures.pop(client_ip, None)
                return False
            return len(q) >= self.max_failures

    def record_failure(self, client_ip: str) -> int:
        now = time.time()
        with self._lock:
            q = self._failures[client_ip]
            self._prune(q, now)
            q.append(now)
            return len(q)

    def clear(self, client_ip: str) -> None:
        with self._lock:
            self._failures.pop(client_ip, None)


def _http_status_from_result(result: dict, *, success_default: int = 200, error_default: int = 502) -> int:
    # Some domain results use `status` for a state name ("CANCELLED"), not HTTP code.
    raw = result.get("status")
    numeric = int(raw) if isinstance(raw, int) else (
        int(raw) if isinstance(raw, str) and raw.isdigit() else 0)
    if result.get("ok"):
        return numeric if 200 <= numeric <= 299 else success_default
    if result.get("conflict"):
        return 409
    if numeric in (400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503):
        return numeric
    return error_default


def make_handler(
    db,
    api_token: str,
    github_service: GitHubExecutionService | None = None,
    *,
    engine=None,
    max_upload_mb: int = 20,
    auth_max_failures: int = 20,
    auth_window_seconds: float = 60.0,
    tls_enabled: bool = False,
):
    """Build a fail-closed authenticated request handler class.

    Raises ValueError immediately if `api_token` is missing, empty, short, or weak.
    """
    ok_token, token_reason = validate_api_token(api_token, min_length=MIN_API_TOKEN_LENGTH)
    if not ok_token:
        raise ValueError(f"Refusing to initialize API handler: {token_reason}")

    expected_token_bytes = api_token.encode("utf-8")
    approvals = engine.approvals if engine is not None else ApprovalManager(db)
    gh_svc = github_service
    if gh_svc is not None and gh_svc.approvals is None:
        gh_svc.approvals = approvals
    limiter = AuthRateLimiter(max_failures=auth_max_failures, window_seconds=auth_window_seconds)
    effective_upload_mb = (
        int(getattr(engine.settings, "max_upload_mb", max_upload_mb))
        if engine is not None
        else max(1, int(max_upload_mb))
    )
    # Account for ~33% base64 encoding overhead plus JSON envelope
    max_upload_body_bytes = int(effective_upload_mb * 1024 * 1024 * 1.45) + 65536
    bg_jobs: dict[str, dict] = {}
    bg_lock = threading.Lock()

    def _enrich_project(proj: dict) -> dict:
        pid = proj["id"]
        tasks = db.list_tasks(pid)
        counts = {"total": len(tasks), "completed": 0, "executing": 0, "queued": 0, "failed": 0, "skipped": 0}
        for t in tasks:
            s = (t.get("status") or "").upper()
            if s == "COMPLETED":
                counts["completed"] += 1
            elif s == "EXECUTING":
                counts["executing"] += 1
            elif s == "QUEUED":
                counts["queued"] += 1
            elif s == "FAILED":
                counts["failed"] += 1
            elif s == "SKIPPED":
                counts["skipped"] += 1
        try:
            meta = json.loads(proj.get("metadata") or "{}")
        except Exception:
            meta = {}
        repo = db.get_repository(pid)
        pending = db.pending_approvals(pid)
        out = dict(proj)
        out["software_type"] = meta.get("software_type") or ""
        out["complexity"] = meta.get("complexity") or ""
        out["description"] = proj.get("description") or meta.get("description") or ""
        out["created_via"] = meta.get("created_via") or "system"
        out["task_counts"] = counts
        out["repository"] = repo
        out["pending_approvals_count"] = len(pending)
        return out

    def _enrich_tasks(pid: str) -> list[dict]:
        tasks = db.list_tasks(pid)
        execs = db.list_executions(pid)
        execs_by_task: dict[str, list[dict]] = defaultdict(list)
        for ex in execs:
            execs_by_task[ex["task_id"]].append(ex)
        out = []
        for t in tasks:
            try:
                payload = json.loads(t.get("payload") or "{}")
            except Exception:
                payload = {}
            tex = execs_by_task.get(t["id"], [])
            last_ex = tex[-1] if tex else None
            out.append({
                "id": t["id"],
                "project_id": t["project_id"],
                "agent_id": t["agent_id"],
                "agent_name": AGENT_NAMES.get(t["agent_id"], t["agent_id"]),
                "title": t["title"],
                "status": t["status"],
                "priority": t["priority"],
                "result": t.get("result") or "",
                "error": t.get("error") or "",
                "retries": t.get("retries", 0),
                "max_retries": t.get("max_retries", 3),
                "cancel_requested": bool(t.get("cancel_requested", 0)),
                "not_before": t.get("not_before", 0),
                "source": payload.get("source") or "orchestrator",
                "instruction_preview": (payload.get("instruction") or "")[:300],
                "depends_on": db.task_dependencies(t["id"]),
                "execution_count": len(tex),
                "duration_ms": last_ex.get("duration_ms") if last_ex else None,
                "created_at": t["created_at"],
                "updated_at": t["updated_at"],
            })
        return out

    def _agent_activity() -> dict:
        db_agents = {r["id"]: r for r in db.query("SELECT * FROM agents")}
        stat_rows = db.query(
            "SELECT agent_id, status, COUNT(*) n FROM tasks GROUP BY agent_id, status"
        )
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in stat_rows:
            counts[r["agent_id"]][r["status"]] = int(r["n"])
        running_tasks = {
            r["agent_id"]: {"task_id": r["id"], "project_id": r["project_id"], "title": r["title"]}
            for r in db.query("SELECT id, project_id, agent_id, title FROM tasks WHERE status='EXECUTING'")
        }
        agents_out = []
        for aid, name in AGENT_NAMES.items():
            row = db_agents.get(aid) or {}
            c = counts.get(aid, {})
            cur = running_tasks.get(aid)
            st = "RUNNING" if cur else (row.get("status") or "IDLE")
            agents_out.append({
                "id": aid,
                "name": name,
                "status": st,
                "last_run_at": row.get("last_run_at"),
                "current_task": cur,
                "tasks_queued": c.get("QUEUED", 0),
                "tasks_executing": c.get("EXECUTING", 0),
                "tasks_completed": c.get("COMPLETED", 0),
                "tasks_failed": c.get("FAILED", 0),
            })
        active_execs = db.query(
            "SELECT id, project_id, task_id, agent_id, status, started_at "
            "FROM executions WHERE status='RUNNING' ORDER BY started_at DESC LIMIT 20"
        )
        return {"agents": agents_out, "active_executions": active_execs}

    def _require_github() -> tuple[GitHubExecutionService | None, tuple[int, dict] | None]:
        if gh_svc is None:
            return None, (503, {"ok": False, "error": "GitHub execution service is not configured on backend"})
        return gh_svc, None

    class APIRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            log.info("api %s %s", self.client_address[0] if self.client_address else "-", redact(fmt % args))

        def _client_ip(self) -> str:
            return str(self.client_address[0]) if self.client_address else "unknown"

        def _send(self, code: int, obj: dict, *, extra_headers: dict[str, str] | None = None) -> None:
            payload = dict(obj)
            if "error" in payload and payload["error"] is not None:
                payload["error"] = redact(str(payload["error"]))
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(int(code))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.send_header("Connection", "close")
            fwd_proto = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
            if tls_enabled or fwd_proto == "https":
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(hk, hv)
            self.end_headers()
            self.wfile.write(raw)

        def _require_auth(self) -> bool:
            ip = self._client_ip()
            if limiter.is_blocked(ip):
                log.warning("rate-limited unauthenticated client %s on %s %s", ip, self.command, self.path.split("?")[0])
                self._send(
                    429,
                    {"ok": False, "error": "too many failed authentication attempts; retry later"},
                    extra_headers={"Retry-After": str(int(limiter.window_seconds))},
                )
                return False

            auth_header = self.headers.get("Authorization")
            valid = False
            if isinstance(auth_header, str) and auth_header.startswith("Bearer "):
                candidate = auth_header[7:]
                if candidate and "\x00" not in candidate and "\r" not in candidate and "\n" not in candidate:
                    valid = hmac.compare_digest(candidate.encode("utf-8"), expected_token_bytes)

            if not valid:
                limiter.record_failure(ip)
                log.warning("rejected unauthorized request from %s on %s %s", ip, self.command, self.path.split("?")[0])
                self._send(
                    401,
                    {"ok": False, "error": "unauthorized"},
                    extra_headers={"WWW-Authenticate": 'Bearer realm="phone-ai-agent-team"'},
                )
                return False

            limiter.clear(ip)
            return True

        def _read_json(self, *, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> tuple[dict | None, bool]:
            """Parse JSON request body with strict Content-Length limit. Returns (body, sent_error)."""
            raw_len = self.headers.get("Content-Length")
            if raw_len is None or raw_len.strip() == "":
                return {}, False
            try:
                length = int(raw_len.strip())
            except ValueError:
                self._send(400, {"ok": False, "error": "invalid Content-Length header"})
                return None, True
            if length < 0:
                self._send(400, {"ok": False, "error": "negative Content-Length header"})
                return None, True
            if length > max_bytes:
                self._send(413, {"ok": False, "error": f"request body exceeds limit ({max_bytes} bytes)"})
                return None, True
            if length == 0:
                return {}, False
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"ok": False, "error": "malformed JSON request body"})
                return None, True
            if not isinstance(parsed, dict):
                self._send(400, {"ok": False, "error": "JSON request body must be an object"})
                return None, True
            return parsed, False

        def _parsed_route(self) -> tuple[list[str], dict[str, list[str]]]:
            parsed = urlparse(self.path)
            clean_path = parsed.path.strip("/")
            parts = [unquote(seg) for seg in clean_path.split("/") if seg]
            query = parse_qs(parsed.query, keep_blank_values=False)
            return parts, query

        @staticmethod
        def _q1(q: dict[str, list[str]], key: str, default: str = "") -> str:
            vals = q.get(key)
            return unquote(vals[0]) if vals else default

        # -------------------- GET --------------------
        def do_GET(self) -> None:
            if not self._require_auth():
                return
            try:
                self._handle_get()
            except PermissionError as pe:
                self._send(403, {"ok": False, "error": str(pe)})
            except Exception as e:
                log.exception("GET handler error")
                self._send(500, {"ok": False, "error": f"internal server error: {redact(str(e))}"})

        def _handle_get(self) -> None:
            parts, q = self._parsed_route()

            # /api/health
            if parts == ["api", "health"]:
                payload: dict = {
                    "ok": True,
                    "version": API_VERSION,
                    "agents": len(AGENT_NAMES),
                    "authenticated": True,
                    "github_configured": bool(gh_svc and gh_svc.api and gh_svc.api.token),
                    "orchestrator_attached": engine is not None,
                    "telegram_connected": bool(engine is not None and engine._on_approval is not None),
                }
                if engine is not None:
                    rh = engine.router.health()
                    snap = engine.resources.snapshot()
                    payload.update({
                        "ai_mode": rh["mode"],
                        "providers": {k: {"status": v["status"], "model": v["model"]} for k, v in rh["providers"].items()},
                        "active_workers": engine.scheduler.active_count(),
                        "resources": {
                            "available_ram_mb": snap["memory"]["available_mb"],
                            "free_disk_mb": snap["storage"]["free_mb"],
                            "is_termux": snap["is_termux"],
                        },
                    })
                return self._send(200, payload)

            # Account discovery uses the server-side GitHub credential; it never returns the token.
            if parts in (["api", "github", "account"], ["api", "github", "repositories"]):
                svc, gh_err = _require_github()
                if gh_err:
                    return self._send(*gh_err)
                assert svc is not None
                result = (svc.api.authenticated_user() if parts[-1] == "account"
                          else svc.api.accessible_repositories())
                return self._send(_http_status_from_result(result), result)

            # /api/agents
            if parts == ["api", "agents"]:
                act = _agent_activity()
                return self._send(200, {"ok": True, "count": len(act["agents"]), **act})

            # /api/logs
            if parts == ["api", "logs"]:
                pid = self._q1(q, "project_id") or None
                tid = self._q1(q, "task_id") or None
                eid = self._q1(q, "execution_id") or None
                try:
                    limit = int(self._q1(q, "limit", "100"))
                except ValueError:
                    limit = 100
                logs = db.list_project_logs(pid, task_id=tid, execution_id=eid, limit=limit)
                return self._send(200, {"ok": True, "logs": logs})

            # /api/sync
            if parts == ["api", "sync"]:
                pid = self._q1(q, "project_id") or None
                try:
                    limit = int(self._q1(q, "limit", "100"))
                except ValueError:
                    limit = 100
                return self._send(200, {"ok": True, "events": db.list_sync_events(pid, limit=limit)})

            # /api/approvals
            if parts == ["api", "approvals"]:
                pid = self._q1(q, "project_id") or None
                status = self._q1(q, "status", "PENDING").upper()
                if status == "ALL":
                    rows = db.list_approvals(project_id=pid, status=None, limit=100)
                elif status == "PENDING":
                    rows = db.pending_approvals(pid)
                else:
                    rows = db.list_approvals(project_id=pid, status=status, limit=100)
                return self._send(200, {"ok": True, "approvals": rows})

            # /api/approvals/audit
            if parts == ["api", "approvals", "audit"]:
                pid = self._q1(q, "project_id") or None
                return self._send(200, {"ok": True, "audit_logs": approvals.audit_logs(pid, limit=100)})

            # /api/projects
            if parts == ["api", "projects"]:
                projects = [_enrich_project(p) for p in db.list_projects()]
                return self._send(200, {"ok": True, "projects": projects})

            # /api/projects/{pid}[/...]
            if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                pid = parts[2]
                proj = db.get_project(pid)
                if not proj:
                    return self._send(404, {"ok": False, "error": "project not found"})

                # GET /api/projects/{pid}
                if len(parts) == 3:
                    mem = db.get_memory(pid)
                    blueprint_preview = ""
                    ws = proj.get("workspace") or ""
                    if ws and Path(ws).is_dir():
                        fm = FileManager(ws)
                        if fm.exists("requirements.txt"):
                            blueprint_preview = redact(fm.read("requirements.txt", max_bytes=16_000))[:8000]
                    with bg_lock:
                        job = bg_jobs.get(pid)
                    return self._send(200, {
                        "ok": True,
                        "project": _enrich_project(proj),
                        "lifecycle": proj.get("lifecycle") or proj.get("status") or "IDLE",
                        "blueprint_analysis": mem.get("blueprint_analysis") or {},
                        "blueprint_preview": blueprint_preview,
                        "repository": db.get_repository(pid),
                        "tasks": _enrich_tasks(pid),
                        "executions": db.list_executions(pid)[-50:],
                        "uploads": db.list_uploaded_artifacts(pid, limit=50),
                        "pull_requests": db.list_pull_requests(pid),
                        "pending_approvals": db.pending_approvals(pid),
                        "recent_logs": db.list_project_logs(pid, limit=50),
                        "sync_events": db.list_sync_events(pid, limit=30),
                        "github_job": job,
                        "report": engine.report(pid) if engine is not None else "",
                    })

                sub = parts[3]

                # GET /api/projects/{pid}/tasks
                if len(parts) == 4 and sub == "tasks":
                    return self._send(200, {
                        "ok": True,
                        "project_id": pid,
                        "lifecycle": proj.get("lifecycle") or "IDLE",
                        "tasks": _enrich_tasks(pid),
                    })

                # GET /api/projects/{pid}/tasks/{tid}
                if len(parts) == 5 and sub == "tasks":
                    tid = parts[4]
                    task = db.get_task(tid)
                    if not task or task["project_id"] != pid:
                        return self._send(404, {"ok": False, "error": "task not found"})
                    execs = db.query(
                        "SELECT * FROM executions WHERE project_id=? AND task_id=? ORDER BY started_at ASC",
                        (pid, tid),
                    )
                    logs = db.list_project_logs(pid, task_id=tid, limit=200)
                    handoffs = [h for h in db.list_handoffs(pid) if h.get("from_task_id") == tid]
                    try:
                        out_data = json.loads(task.get("output_data") or "{}")
                    except Exception:
                        out_data = {}
                    return self._send(200, {
                        "ok": True,
                        "task": task,
                        "agent_name": AGENT_NAMES.get(task["agent_id"], task["agent_id"]),
                        "depends_on": db.task_dependencies(tid),
                        "output_data": out_data,
                        "executions": execs,
                        "logs": logs,
                        "handoffs": handoffs,
                        "control_events": db.list_task_control_events(tid, limit=50),
                        "memory_history": db.memory_entry_history(pid, "task_result", tid)
                        + db.memory_entry_history(pid, "task_failure", tid),
                    })

                # GET /api/projects/{pid}/uploads
                if len(parts) == 4 and sub == "uploads":
                    return self._send(200, {"ok": True, "uploads": db.list_uploaded_artifacts(pid, limit=100)})

                # GET /api/projects/{pid}/logs
                if len(parts) == 4 and sub == "logs":
                    tid = self._q1(q, "task_id") or None
                    try:
                        limit = int(self._q1(q, "limit", "100"))
                    except ValueError:
                        limit = 100
                    return self._send(200, {"ok": True, "logs": db.list_project_logs(pid, task_id=tid, limit=limit)})

                # GET /api/projects/{pid}/sync
                if len(parts) == 4 and sub == "sync":
                    try:
                        limit = int(self._q1(q, "limit", "100"))
                    except ValueError:
                        limit = 100
                    return self._send(200, {"ok": True, "events": db.list_sync_events(pid, limit=limit)})

                # GET /api/projects/{pid}/memory — structured, versioned project knowledge
                if len(parts) == 4 and sub == "memory":
                    try:
                        limit = int(self._q1(q, "limit", "100"))
                    except ValueError:
                        limit = 100
                    return self._send(200, {
                        "ok": True,
                        "project_id": pid,
                        "facts": db.get_memory(pid),
                        "entries": db.search_memory_entries(
                            pid, kind=self._q1(q, "kind"), query=self._q1(q, "q"), limit=limit,
                            include_history=self._q1(q, "history").lower() in ("1", "true", "yes")),
                    })

                # GET /api/projects/{pid}/repository
                if len(parts) == 4 and sub == "repository":
                    repo = db.get_repository(pid)
                    if not repo:
                        return self._send(404, {"ok": False, "error": "no repository connected"})
                    return self._send(200, {"ok": True, "repository": repo})

                # GitHub-backed sub-resources
                if sub in ("branches", "branch-status", "commits", "files", "file", "diff", "pull-requests"):
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    repo = db.get_repository(pid)
                    if not repo:
                        return self._send(404, {"ok": False, "error": "no repository connected"})

                    if len(parts) == 4 and sub == "branches":
                        r = svc.list_branches(pid)
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "branch-status":
                        r = svc.branch_status(
                            pid,
                            branch=self._q1(q, "branch") or None,
                            base=self._q1(q, "base") or None,
                        )
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "commits":
                        target_br, berr = svc.resolve_working_branch(pid, self._q1(q, "branch") or None)
                        if berr:
                            return self._send(_http_status_from_result(berr, error_default=400), berr)
                        try:
                            per_page = int(self._q1(q, "per_page", "30"))
                        except ValueError:
                            per_page = 30
                        r = svc.api.commits(repo["repo_full_name"], target_br, per_page=per_page)
                        if r.get("ok"):
                            r["branch"] = target_br
                            r["default_branch"] = repo.get("default_branch") or ""
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "files":
                        r = svc.list_files(pid, path=self._q1(q, "path", ""), branch=self._q1(q, "branch") or None)
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "file":
                        r = svc.read_user_file(pid, path=self._q1(q, "path", ""), branch=self._q1(q, "branch") or None)
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "diff":
                        r = svc.compare_branches(
                            pid,
                            base=self._q1(q, "base") or None,
                            head=self._q1(q, "head") or None,
                        )
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 4 and sub == "pull-requests":
                        r = svc.list_pull_requests(pid, state=self._q1(q, "state", "all"))
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 5 and sub == "pull-requests":
                        r = svc.get_pull_request_detail(pid, parts[4])
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 6 and sub == "pull-requests" and parts[5] == "files":
                        r = svc.list_pull_request_files(pid, parts[4])
                        return self._send(_http_status_from_result(r), r)

                    if len(parts) == 6 and sub == "pull-requests" and parts[5] == "reviews":
                        revs = svc.api.pr_reviews(repo["repo_full_name"], parts[4])
                        if not revs.get("ok"):
                            return self._send(_http_status_from_result(revs), revs)
                        cmts = svc.api.pr_comments(repo["repo_full_name"], parts[4])
                        return self._send(200, {
                            "ok": True,
                            "reviews": revs.get("data", []),
                            "comments": cmts.get("data", []) if cmts.get("ok") else [],
                        })

            self._send(404, {"ok": False, "error": "not found"})

        # -------------------- POST --------------------
        def do_POST(self) -> None:
            if not self._require_auth():
                return
            try:
                self._handle_post()
            except PermissionError as pe:
                self._send(403, {"ok": False, "error": str(pe)})
            except Exception as e:
                log.exception("POST handler error")
                self._send(500, {"ok": False, "error": f"internal server error: {redact(str(e))}"})

        def _handle_post(self) -> None:
            parts, _ = self._parsed_route()
            is_upload = (len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "uploads")
            body, sent_err = self._read_json(
                max_bytes=max_upload_body_bytes if is_upload else DEFAULT_MAX_JSON_BYTES,
            )
            if sent_err or body is None:
                return

            # POST /api/projects
            if parts == ["api", "projects"]:
                idem_key = str(body.get("idempotency_key") or "").strip()
                if idem_key:
                    cached = db.get_idempotent_response(f"create_proj:{idem_key}")
                    if cached:
                        return self._send(200, {**cached, "deduplicated": True})

                name = str(body.get("name") or "").strip()
                if not name:
                    return self._send(400, {"ok": False, "error": "project name is required"})
                desc = str(body.get("description") or "").strip()[:1000]
                blueprint = str(body.get("blueprint") or body.get("requirements") or "").strip()
                auto_start = bool(body.get("auto_start", bool(blueprint)))
                quick = bool(body.get("quick", False))
                dynamic = bool(body.get("dynamic", True))

                if engine is not None:
                    pid = engine.create_project(
                        name,
                        requirements=blueprint,
                        quick=quick,
                        auto_start=bool(auto_start and blueprint),
                        dynamic=dynamic,
                        source="dashboard",
                        description=desc,
                    )
                else:
                    ws = str(body.get("workspace") or "").strip()
                    pid = db.create_project(name[:80], ws, {"description": desc, "created_via": "dashboard"})
                    if desc:
                        db.execute("UPDATE projects SET description=? WHERE id=?", (desc, pid))
                    db.record_sync(pid, "dashboard", "project_created", name[:80])

                resp = {"ok": True, "id": pid, "project": _enrich_project(db.get_project(pid))}
                if idem_key:
                    db.save_idempotent_response(f"create_proj:{idem_key}", pid, resp)
                return self._send(201, resp)

            # POST /api/approvals/decide
            if parts == ["api", "approvals", "decide"]:
                aid = str(body.get("id") or "").strip()
                if not aid:
                    return self._send(400, {"ok": False, "error": "approval id is required"})
                if "approved" not in body or not isinstance(body.get("approved"), bool):
                    return self._send(400, {"ok": False, "error": "'approved' boolean field is required"})
                approved = bool(body["approved"])
                actor = str(body.get("by") or "dashboard").strip()[:60] or "dashboard"
                ok = approvals.approve(aid, by=actor) if approved else approvals.reject(aid, by=actor)
                row = approvals.get(aid)
                if ok:
                    if engine is not None:
                        engine.on_approval_decided(aid, approved=approved, source=actor)
                    elif row:
                        db.record_sync(
                            row["project_id"], actor,
                            "approval_approved" if approved else "approval_rejected",
                            f"{aid} ({row.get('action', '')})",
                        )
                return self._send(200, {
                    "ok": ok,
                    "id": aid,
                    "status": row["status"] if row else "UNKNOWN",
                    "decided_by": row.get("decided_by") if row else None,
                })

            # /api/projects/{pid}/...
            if len(parts) >= 4 and parts[:2] == ["api", "projects"]:
                pid = parts[2]
                proj = db.get_project(pid)
                if not proj:
                    return self._send(404, {"ok": False, "error": "project not found"})
                sub = parts[3]

                # POST /api/projects/{pid}/instructions  (Instruction Composer & Blueprint Submission)
                if len(parts) == 4 and sub == "instructions":
                    instruction = str(body.get("instruction") or body.get("blueprint") or "").strip()
                    if not instruction:
                        return self._send(400, {"ok": False, "error": "instruction text is required"})
                    if len(instruction) > 250_000:
                        return self._send(413, {"ok": False, "error": "instruction exceeds 250KB limit"})

                    idem_key = str(body.get("idempotency_key") or "").strip()
                    if idem_key:
                        cached = db.get_idempotent_response(f"instr:{pid}:{idem_key}")
                        if cached:
                            return self._send(200, {**cached, "deduplicated": True})

                    mode = str(body.get("mode") or "blueprint").strip().lower()
                    if mode not in ("blueprint", "append", "replan", "github_execute"):
                        return self._send(400, {"ok": False, "error": f"invalid mode: {mode!r}"})
                    quick = bool(body.get("quick", False))
                    dynamic = bool(body.get("dynamic", True))

                    if mode == "github_execute":
                        return self._run_github_execute(pid, body, instruction)

                    if engine is None:
                        tid = db.create_task(
                            pid, "chief_orchestrator", f"Process blueprint ({mode})",
                            {"instruction": instruction[:8000], "mode": mode, "source": "dashboard"},
                        )
                        db.record_sync(pid, "dashboard", "instruction_submitted", f"{mode}:{tid}")
                        resp = {"ok": True, "project_id": pid, "mode": mode, "task_ids": [tid], "task_count": 1}
                    elif mode == "blueprint" and not db.list_tasks(pid):
                        tids = engine.start_project(
                            pid, requirements=instruction, quick=quick, dynamic=dynamic, source="dashboard",
                        )
                        analysis = db.get_memory(pid).get("blueprint_analysis") or {}
                        db.record_sync(pid, "dashboard", "blueprint_submitted", f"{len(tids)} tasks")
                        resp = {
                            "ok": True,
                            "project_id": pid,
                            "mode": mode,
                            "task_ids": tids,
                            "task_count": len(tids),
                            "software_type": analysis.get("software_type", ""),
                            "selected_agents": analysis.get("selected_agents", []),
                            "excluded_agents": analysis.get("excluded_agents", {}),
                        }
                    else:
                        feedback = str(body.get("feedback") or "").strip()
                        info = engine.replan_project(
                            pid, additional_requirements=instruction,
                            execution_feedback=feedback, source="dashboard",
                        )
                        if not info.get("ok"):
                            return self._send(400, info)
                        db.record_sync(pid, "dashboard", "instruction_submitted", f"{mode}:{instruction[:120]}")
                        tasks_now = db.list_tasks(pid)
                        resp = {
                            **info,
                            "project_id": pid,
                            "mode": mode,
                            "task_count": len(tasks_now),
                        }

                    if idem_key:
                        db.save_idempotent_response(f"instr:{pid}:{idem_key}", pid, resp)
                    return self._send(201, resp)

                # POST /api/projects/{pid}/github-execute
                if len(parts) == 4 and sub == "github-execute":
                    instruction = str(body.get("instruction") or body.get("blueprint") or "").strip()
                    if not instruction:
                        return self._send(400, {"ok": False, "error": "instruction is required"})
                    return self._run_github_execute(pid, body, instruction)

                # POST /api/projects/{pid}/uploads  (Blueprint / ZIP / Source / Image / Document)
                if len(parts) == 4 and sub == "uploads":
                    filename = Path(str(body.get("filename") or "").strip()).name
                    raw_b64 = body.get("content_base64")
                    category = str(body.get("category") or "").strip().lower() or None
                    caption = str(body.get("caption") or "").strip()
                    auto_plan = bool(body.get("auto_plan", False))

                    if not filename:
                        return self._send(400, {"ok": False, "error": "filename is required"})
                    if not isinstance(raw_b64, str) or not raw_b64.strip():
                        return self._send(400, {"ok": False, "error": "content_base64 is required"})
                    try:
                        decoded = base64.b64decode(raw_b64.strip(), validate=True)
                    except (binascii.Error, ValueError):
                        return self._send(400, {"ok": False, "error": "invalid base64 upload content"})
                    if len(decoded) == 0:
                        return self._send(400, {"ok": False, "error": "empty file upload rejected"})
                    if len(decoded) > effective_upload_mb * 1024 * 1024:
                        return self._send(413, {"ok": False, "error": f"file exceeds {effective_upload_mb}MB limit"})

                    suffix = Path(filename).suffix[:16]
                    fd, tmp_path = tempfile.mkstemp(prefix="api_upload_", suffix=suffix)
                    os.close(fd)
                    try:
                        Path(tmp_path).write_bytes(decoded)
                        if engine is not None:
                            res = engine.ingest_upload_detailed(
                                pid, tmp_path, filename,
                                auto_plan_if_waiting=auto_plan,
                                source="dashboard",
                                category=category,
                                caption=caption,
                            )
                            return self._send(_http_status_from_result(res, success_default=201, error_default=400), res)

                        # Fallback validation & staging when running DB-only handler in unit tests
                        import hashlib
                        from execution.document_processor import extract_document_text
                        from security.validation import (
                            classify_upload_category,
                            safe_extract_zip,
                            validate_upload,
                            validate_upload_content,
                        )
                        ok_u, msg_u = validate_upload(filename, len(decoded), effective_upload_mb)
                        if not ok_u:
                            return self._send(400, {"ok": False, "error": msg_u})
                        ok_c, msg_c = validate_upload_content(filename, tmp_path)
                        if not ok_c:
                            return self._send(400, {"ok": False, "error": msg_c})
                        digest = hashlib.sha256(decoded).hexdigest()
                        det_cat = classify_upload_category(filename)
                        final_cat = "blueprint" if (category == "blueprint" and det_cat in ("document", "source")) else det_cat
                        ws = Path(proj.get("workspace") or tempfile.gettempdir()) / f"uploads_{pid}"
                        if det_cat == "zip":
                            ok_z, msg_z, files = safe_extract_zip(tmp_path, str(ws / "extracted"))
                            if not ok_z:
                                return self._send(400, {"ok": False, "error": msg_z})
                            uid = db.record_uploaded_artifact(
                                pid, filename, sha256=digest, source="dashboard",
                                category="zip", size_bytes=len(decoded), summary=msg_z,
                            )
                            db.record_sync(pid, "dashboard", "artifact_uploaded", f"zip:{filename}")
                            return self._send(201, {
                                "ok": True, "upload_id": uid, "filename": filename, "category": "zip",
                                "size_bytes": len(decoded), "sha256": digest,
                                "extracted_files": files[:100], "extracted_count": len(files), "message": msg_z,
                            })
                        doc = extract_document_text(tmp_path, filename)
                        if not doc.get("ok"):
                            return self._send(400, {"ok": False, "error": doc.get("warning") or "invalid file"})
                        uid = db.record_uploaded_artifact(
                            pid, filename, sha256=digest, source="dashboard",
                            category=final_cat, size_bytes=len(decoded), summary=doc["text"][:200],
                        )
                        db.record_sync(pid, "dashboard", "artifact_uploaded", f"{final_cat}:{filename}")
                        return self._send(201, {
                            "ok": True, "upload_id": uid, "filename": filename, "category": final_cat,
                            "kind": doc["kind"], "size_bytes": len(decoded), "sha256": digest,
                            "chars_extracted": doc["chars"], "message": f"Uploaded {filename}",
                        })
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                # POST /api/projects/{pid}/tasks
                if len(parts) == 4 and sub == "tasks":
                    title = str(body.get("title") or "").strip()
                    if not title:
                        return self._send(400, {"ok": False, "error": "task title is required"})
                    agent_id = str(body.get("agent_id") or "chief_orchestrator").strip()
                    if agent_id not in AGENT_NAMES:
                        return self._send(400, {"ok": False, "error": f"unknown agent_id: {agent_id!r}"})
                    instruction = str(body.get("instruction") or "").strip()
                    auto_run = bool(body.get("auto_run", True))
                    try:
                        priority = int(body.get("priority", 100))
                    except (TypeError, ValueError):
                        priority = 100
                    deps = body.get("depends_on") if isinstance(body.get("depends_on"), list) else None

                    idem_key = str(body.get("idempotency_key") or "").strip()
                    if idem_key:
                        cached = db.get_idempotent_response(f"task:{pid}:{idem_key}")
                        if cached:
                            return self._send(200, {**cached, "deduplicated": True})

                    if engine is not None:
                        res = engine.create_single_task(
                            pid, agent_id, title, instruction,
                            priority=priority, depends_on=deps, auto_run=auto_run, source="dashboard",
                            idempotency_key=idem_key,
                        )
                        code = _http_status_from_result(res, success_default=201, error_default=400)
                        if res.get("ok") and idem_key:
                            db.save_idempotent_response(f"task:{pid}:{idem_key}", pid, res)
                        return self._send(code, res)

                    existing = db.query_one(
                        "SELECT id, status FROM tasks WHERE project_id=? AND agent_id=? AND title=?",
                        (pid, agent_id, title[:200]),
                    )
                    if existing:
                        return self._send(200, {
                            "ok": True, "id": existing["id"], "task_status": existing["status"], "deduplicated": True,
                        })
                    tid = db.create_task(
                        pid, agent_id, title[:200],
                        {"instruction": instruction[:8000], "source": "dashboard"},
                        priority=priority, depends_on=deps, idempotency_key=idem_key,
                    )
                    db.record_sync(pid, "dashboard", "task_created", f"{tid} ({agent_id}: {title[:80]})")
                    resp = {"ok": True, "id": tid, "task_status": "QUEUED", "deduplicated": False}
                    if idem_key:
                        db.save_idempotent_response(f"task:{pid}:{idem_key}", pid, resp)
                    return self._send(201, resp)

                # POST /api/projects/{pid}/tasks/{tid}/control — owner task control
                if len(parts) == 6 and sub == "tasks" and parts[5] == "control":
                    tid = parts[4]
                    action = str(body.get("action") or "").strip().lower()
                    if action not in ("cancel", "retry", "resume"):
                        return self._send(400, {"ok": False, "error": "action must be cancel, retry or resume"})
                    task = db.get_task(tid)
                    if not task or task["project_id"] != pid:
                        return self._send(404, {"ok": False, "error": "task not found in this project"})
                    idem_key = str(body.get("idempotency_key") or "").strip()
                    cache_key = f"task-control:{pid}:{tid}:{idem_key}"
                    if idem_key:
                        cached = db.get_idempotent_response(cache_key)
                        if cached:
                            return self._send(200, {**cached, "deduplicated": True})
                    if engine is None:
                        return self._send(503, {"ok": False, "error": "orchestrator engine not attached"})
                    res = engine.control_task(
                        pid, tid, action, actor=str(body.get("actor") or "dashboard")[:80])
                    code = _http_status_from_result(res, success_default=200, error_default=409)
                    if code < 400 and idem_key:
                        db.save_idempotent_response(cache_key, pid, res)
                    return self._send(code, res)

                # POST /api/projects/{pid}/memory — add a structured project fact
                if len(parts) == 4 and sub == "memory":
                    kind = str(body.get("kind") or "").strip()
                    key = str(body.get("key") or "").strip()
                    value = str(body.get("value") or "").strip()
                    if not kind or not key or not value:
                        return self._send(400, {"ok": False, "error": "kind, key and value are required"})
                    if engine is not None:
                        res = engine.memory.remember_entry(
                            pid, kind, key, value,
                            source=str(body.get("source") or "dashboard")[:120],
                            task_id=str(body.get("task_id") or "")[:80])
                    else:
                        res = db.remember_entry(pid, kind, key, value,
                                                source=str(body.get("source") or "dashboard")[:120])
                    if not res.get("ok"):
                        return self._send(400, res)
                    db.record_sync(pid, "dashboard", "memory_written", f"{kind}:{key[:80]}")
                    entry = db.search_memory_entries(pid, kind=kind, limit=1)
                    return self._send(200, {**res, "entry": entry[0] if entry else None})

                # POST /api/projects/{pid}/control
                if len(parts) == 4 and sub == "control":
                    if engine is None:
                        return self._send(503, {"ok": False, "error": "orchestrator engine not attached"})
                    act = str(body.get("action") or "").strip().lower()
                    if act == "build":
                        r = engine.replan_project(pid, source="dashboard")
                        return self._send(200 if r.get("ok") else 400, r)
                    if act == "pause":
                        engine.pause()
                        db.record_sync(pid, "dashboard", "workers_paused", "")
                        return self._send(200, {"ok": True, "action": "pause"})
                    if act == "resume":
                        engine.resume()
                        db.record_sync(pid, "dashboard", "workers_resumed", "")
                        return self._send(200, {"ok": True, "action": "resume"})
                    if act == "cancel":
                        engine.cancel(pid)
                        db.record_sync(pid, "dashboard", "project_cancelled", "")
                        return self._send(200, {"ok": True, "action": "cancel", "lifecycle": "CANCELLED"})
                    if act == "recover":
                        r = engine.recover(pid)
                        db.record_sync(pid, "dashboard", "project_recovered", "")
                        return self._send(200 if r.get("ok") else 400, r)
                    return self._send(400, {"ok": False, "error": f"unsupported control action: {act!r}"})

                # POST /api/projects/{pid}/approvals
                if len(parts) == 4 and sub == "approvals":
                    action = str(body.get("action") or "").strip()
                    detail = str(body.get("detail") or "").strip()
                    if not action:
                        return self._send(400, {"ok": False, "error": "action is required"})
                    try:
                        aid = approvals.require(pid, action, detail, task_id=body.get("task_id"))
                    except ValueError as ve:
                        return self._send(400, {"ok": False, "error": str(ve)})
                    db.record_sync(pid, "dashboard", "approval_requested", f"{aid} ({action})")
                    return self._send(201, {"ok": True, "id": aid, "approval": approvals.get(aid)})

                # POST /api/projects/{pid}/repository
                if len(parts) == 4 and sub == "repository":
                    repo_name = str(body.get("repo") or "").strip()
                    ok_r, err_r = validate_repo_full_name(repo_name)
                    if not ok_r:
                        return self._send(400, {"ok": False, "error": err_r})
                    def_br = str(body.get("default_branch") or "").strip() or None
                    wrk_br = str(body.get("working_branch") or "").strip() or None
                    vis = str(body.get("visibility") or "").strip() or None

                    if gh_svc is not None:
                        r = gh_svc.connect_repository(
                            pid, repo_name, visibility=vis, default_branch=def_br,
                            working_branch=wrk_br, source="dashboard",
                        )
                        return self._send(_http_status_from_result(r), r)

                    try:
                        binding = db.bind_repository(
                            pid, repo_name, vis or "private", def_br or "", wrk_br,
                        )
                    except ValueError as ve:
                        return self._send(400, {"ok": False, "error": str(ve)})
                    db.record_sync(pid, "dashboard", "repository_connected", repo_name)
                    return self._send(200, {"ok": True, "repository": binding, "remote_verified": False})

                # POST /api/projects/{pid}/repository/refresh
                if len(parts) == 5 and sub == "repository" and parts[4] == "refresh":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    r = svc.refresh_repository(pid, source="dashboard")
                    return self._send(_http_status_from_result(r), r)

                # POST /api/projects/{pid}/branches  (create branch)
                if len(parts) == 4 and sub == "branches":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    bname = str(body.get("name") or body.get("branch") or "").strip()
                    from_br = str(body.get("from_branch") or body.get("base") or "").strip() or None
                    select_w = bool(body.get("select_working", True))
                    r = svc.create_branch(pid, bname, from_branch=from_br, select_working=select_w, source="dashboard")
                    return self._send(_http_status_from_result(r, success_default=201), r)

                # POST /api/projects/{pid}/file  (create new file — refuses to overwrite existing)
                if len(parts) == 4 and sub == "file":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    fpath = str(body.get("path") or "")
                    content = str(body.get("content") or "")
                    branch = str(body.get("branch") or "").strip() or None
                    msg = str(body.get("message") or "").strip() or None
                    r = svc.create_user_file(pid, branch, fpath, content, msg, source="dashboard")
                    return self._send(_http_status_from_result(r, success_default=201), r)

                # POST /api/projects/{pid}/file/rename
                if len(parts) == 5 and sub == "file" and parts[4] == "rename":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    old_p = str(body.get("old_path") or "")
                    new_p = str(body.get("new_path") or "")
                    sha = str(body.get("sha") or "").strip() or None
                    branch = str(body.get("branch") or "").strip() or None
                    msg = str(body.get("message") or "").strip() or None
                    r = svc.rename_user_file(pid, branch, old_p, new_p, sha=sha, message=msg, source="dashboard")
                    return self._send(_http_status_from_result(r), r)

                # POST /api/projects/{pid}/pull-requests  (create PR)
                if len(parts) == 4 and sub == "pull-requests":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    title = str(body.get("title") or "").strip()
                    head = str(body.get("head") or body.get("branch") or "").strip()
                    base = str(body.get("base") or "").strip() or None
                    pr_body = str(body.get("body") or "") if "body" in body else None
                    task_id = str(body.get("task_id") or "").strip()
                    draft = bool(body.get("draft", False))
                    r = svc.open_pull_request(
                        pid, task_id, head, title,
                        base_branch=base, body=pr_body, draft=draft, source="dashboard",
                    )
                    return self._send(_http_status_from_result(r, success_default=201), r)

                # POST /api/projects/{pid}/pull-requests/{number}/comments
                if len(parts) == 6 and sub == "pull-requests" and parts[5] == "comments":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    cbody = str(body.get("body") or "")
                    cpath = str(body.get("path") or "").strip() or None
                    cline = body.get("line")
                    cid = str(body.get("commit_id") or "").strip() or None
                    r = svc.comment_pull_request(
                        pid, parts[4], cbody, path=cpath, line=cline, commit_id=cid, source="dashboard",
                    )
                    return self._send(_http_status_from_result(r, success_default=201), r)

                # POST /api/projects/{pid}/pull-requests/{number}/reviews
                if len(parts) == 6 and sub == "pull-requests" and parts[5] == "reviews":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    event = str(body.get("event") or "COMMENT")
                    rbody = str(body.get("body") or "")
                    inline = body.get("comments") if isinstance(body.get("comments"), list) else None
                    r = svc.review_pull_request(
                        pid, parts[4], event, body=rbody, comments=inline, source="dashboard",
                    )
                    return self._send(_http_status_from_result(r, success_default=200), r)

                # POST /api/projects/{pid}/pull-requests/{number}/request-merge-approval
                if len(parts) == 6 and sub == "pull-requests" and parts[5] == "request-merge-approval":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    r = svc.request_merge_approval(
                        pid, parts[4],
                        title=str(body.get("title") or ""),
                        head=str(body.get("head") or ""),
                        base=str(body.get("base") or ""),
                        source="dashboard",
                    )
                    return self._send(_http_status_from_result(r, success_default=201), r)

                # POST /api/projects/{pid}/pull-requests/{number}/merge
                if len(parts) == 6 and sub == "pull-requests" and parts[5] == "merge":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    approval_id = str(body.get("approval_id") or "").strip() or None
                    method = str(body.get("merge_method") or "merge")
                    ctitle = str(body.get("commit_title") or "").strip() or None
                    cmsg = str(body.get("commit_message") or "").strip() or None
                    head_sha = str(body.get("sha") or "").strip() or None
                    actor = str(body.get("by") or "dashboard").strip()[:60] or "dashboard"
                    r = svc.merge(
                        pid, parts[4], approval_id,
                        merge_method=method, commit_title=ctitle, commit_message=cmsg,
                        sha=head_sha, actor=actor,
                    )
                    if r.get("merged") and engine is not None:
                        engine._notify(pid, f"🔀 PR #{parts[4]} merged ({method}) using approval `{approval_id}`.")
                    return self._send(_http_status_from_result(r), r)

            self._send(404, {"ok": False, "error": "not found"})

        def _run_github_execute(self, pid: str, body: dict, instruction: str) -> None:
            if engine is None:
                return self._send(503, {"ok": False, "error": "orchestrator engine not attached"})
            svc, gh_err = _require_github()
            if gh_err:
                return self._send(*gh_err)
            assert svc is not None
            if not db.get_repository(pid):
                return self._send(400, {"ok": False, "error": "connect a GitHub repository to this project first"})

            kwargs = {
                "branch_name": str(body.get("branch") or "").strip() or None,
                "use_existing_branch": bool(body.get("use_existing_branch", False)),
                "base_branch": str(body.get("base_branch") or "").strip() or None,
                "target_prefix": str(body.get("target_prefix") or "").strip(),
                "open_pr": bool(body.get("open_pr", True)),
                "pr_title": str(body.get("pr_title") or "").strip() or None,
                "quick": bool(body.get("quick", False)),
                "dynamic": bool(body.get("dynamic", True)),
                "source": "dashboard",
            }
            with bg_lock:
                cur = bg_jobs.get(pid)
                if cur and cur.get("status") == "RUNNING":
                    return self._send(409, {
                        "ok": False, "conflict": True,
                        "error": "A GitHub agent execution job is already running for this project",
                    })
                bg_jobs[pid] = {"status": "RUNNING", "started_at": time.time(), "result": None}
            if bool(body.get("async_exec", False)):
                def _worker() -> None:
                    try:
                        out = svc.execute_project_on_github(engine, pid, instruction, **kwargs)
                        with bg_lock:
                            bg_jobs[pid] = {
                                "status": "COMPLETED" if out.get("ok") else "FAILED",
                                "finished_at": time.time(),
                                "result": out,
                            }
                    except Exception as exc:
                        with bg_lock:
                            bg_jobs[pid] = {
                                "status": "FAILED",
                                "finished_at": time.time(),
                                "result": {"ok": False, "error": redact(str(exc))},
                            }

                threading.Thread(target=_worker, daemon=True, name=f"gh-exec-{pid[:12]}").start()
                return self._send(202, {"ok": True, "status": "RUNNING", "project_id": pid, "async": True})

            try:
                out = svc.execute_project_on_github(engine, pid, instruction, **kwargs)
            except Exception as exc:
                with bg_lock:
                    bg_jobs[pid] = {"status": "FAILED", "finished_at": time.time(),
                                    "result": {"ok": False, "error": redact(str(exc))}}
                raise
            with bg_lock:
                bg_jobs[pid] = {"status": "COMPLETED" if out.get("ok") else "FAILED",
                                "finished_at": time.time(), "result": out}
            return self._send(_http_status_from_result(out), out)

        # -------------------- PUT / DELETE --------------------
        def do_PUT(self) -> None:
            if not self._require_auth():
                return
            try:
                self._handle_put()
            except PermissionError as pe:
                self._send(403, {"ok": False, "error": str(pe)})
            except Exception as e:
                log.exception("PUT handler error")
                self._send(500, {"ok": False, "error": f"internal server error: {redact(str(e))}"})

        def _handle_put(self) -> None:
            parts, _ = self._parsed_route()
            body, sent_err = self._read_json()
            if sent_err or body is None:
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"]:
                pid = parts[2]
                if not db.get_project(pid):
                    return self._send(404, {"ok": False, "error": "project not found"})
                sub = parts[3]

                # PUT /api/projects/{pid}/branch  (select working branch)
                if sub == "branch":
                    wbranch = str(body.get("working_branch") or body.get("branch") or "").strip()
                    ok_b, err_b = validate_branch_name(wbranch)
                    if not ok_b:
                        return self._send(400, {"ok": False, "error": err_b})
                    if gh_svc is not None:
                        r = gh_svc.select_working_branch(
                            pid, wbranch, verify_remote=bool(body.get("verify_remote", True)), source="dashboard",
                        )
                        return self._send(_http_status_from_result(r), r)
                    try:
                        updated = db.set_working_branch(pid, wbranch, source="dashboard")
                    except ValueError as ve:
                        return self._send(400, {"ok": False, "error": str(ve)})
                    return self._send(200, {"ok": True, "repository": updated, "working_branch": wbranch})

                # PUT /api/projects/{pid}/file  (edit/save file with stale-SHA check)
                if sub == "file":
                    svc, gh_err = _require_github()
                    if gh_err:
                        return self._send(*gh_err)
                    assert svc is not None
                    fpath = str(body.get("path") or "")
                    content = str(body.get("content") or "")
                    sha = str(body.get("sha") or "").strip() or None
                    branch = str(body.get("branch") or "").strip() or None
                    msg = str(body.get("message") or "").strip() or None
                    allow_create = bool(body.get("create", False))
                    r = svc.save_user_file(
                        pid, branch, fpath, content, sha=sha, message=msg,
                        allow_create=allow_create, source="dashboard",
                    )
                    return self._send(_http_status_from_result(r), r)

            self._send(404, {"ok": False, "error": "not found"})

        def do_DELETE(self) -> None:
            if not self._require_auth():
                return
            try:
                self._handle_delete()
            except PermissionError as pe:
                self._send(403, {"ok": False, "error": str(pe)})
            except Exception as e:
                log.exception("DELETE handler error")
                self._send(500, {"ok": False, "error": f"internal server error: {redact(str(e))}"})

        def _handle_delete(self) -> None:
            parts, _ = self._parsed_route()
            body, sent_err = self._read_json()
            if sent_err or body is None:
                return

            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "file":
                pid = parts[2]
                if not db.get_project(pid):
                    return self._send(404, {"ok": False, "error": "project not found"})
                svc, gh_err = _require_github()
                if gh_err:
                    return self._send(*gh_err)
                assert svc is not None
                fpath = str(body.get("path") or "")
                sha = str(body.get("sha") or "").strip()
                branch = str(body.get("branch") or "").strip() or None
                msg = str(body.get("message") or "").strip() or None
                r = svc.delete_user_file(pid, branch, fpath, sha=sha, message=msg, source="dashboard")
                return self._send(_http_status_from_result(r), r)

            self._send(404, {"ok": False, "error": "not found"})

    return APIRequestHandler


def create_api_server(
    db,
    host: str = "127.0.0.1",
    port: int = 8000,
    api_token: str = "",
    github_service: GitHubExecutionService | None = None,
    *,
    engine=None,
    tls_cert: str = "",
    tls_key: str = "",
    max_upload_mb: int = 20,
    auth_max_failures: int = 20,
    auth_window_seconds: float = 60.0,
) -> ThreadingHTTPServer:
    """Create an authenticated API server. Fails closed BEFORE binding if `api_token` is missing/weak."""
    ok_token, reason = validate_api_token(api_token, min_length=MIN_API_TOKEN_LENGTH)
    if not ok_token:
        exposure = "externally accessible" if not is_loopback_host(host) else "local"
        raise ValueError(f"Refusing to start {exposure} API server on {host}:{port}: {reason}")

    use_tls = bool((tls_cert or "").strip() or (tls_key or "").strip())
    ssl_ctx: ssl.SSLContext | None = None
    if use_tls:
        if not ((tls_cert or "").strip() and (tls_key or "").strip()):
            raise ValueError("Both API_TLS_CERT and API_TLS_KEY must be provided when enabling native TLS")
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(certfile=tls_cert.strip(), keyfile=tls_key.strip())

    svc = github_service
    if svc is None and engine is not None:
        gh_tok = getattr(engine.settings, "github_token", "") or os.getenv("GITHUB_TOKEN", "")
        svc = GitHubExecutionService(db, GitHubAPI(gh_tok), engine.approvals)

    handler_cls = make_handler(
        db,
        api_token,
        github_service=svc,
        engine=engine,
        max_upload_mb=max_upload_mb,
        auth_max_failures=auth_max_failures,
        auth_window_seconds=auth_window_seconds,
        tls_enabled=use_tls,
    )
    srv = ThreadingHTTPServer((host, int(port)), handler_cls)
    if ssl_ctx is not None:
        srv.socket = ssl_ctx.wrap_socket(srv.socket, server_side=True)
    if not is_loopback_host(host) and not use_tls:
        log.warning(
            "API server bound to non-loopback address %s:%d over plaintext HTTP. "
            "For remote/Android traffic, terminate HTTPS via Caddy/nginx/Cloudflare Tunnel "
            "or configure API_TLS_CERT and API_TLS_KEY (preferred: bind 127.0.0.1 behind reverse proxy).",
            host, int(port),
        )
    return srv


def serve(
    db,
    host: str = "127.0.0.1",
    port: int = 8000,
    api_token: str = "",
    github_service: GitHubExecutionService | None = None,
    *,
    engine=None,
    tls_cert: str = "",
    tls_key: str = "",
) -> None:
    """Run the authenticated API server until interrupted (fail-closed if token missing/weak)."""
    srv = create_api_server(
        db, host=host, port=port, api_token=api_token,
        github_service=github_service, engine=engine,
        tls_cert=tls_cert, tls_key=tls_key,
    )
    scheme = "https" if (tls_cert and tls_key) else "http"
    log.info("Authenticated Android/Telegram API listening on %s://%s:%d", scheme, host, srv.server_address[1])
    try:
        srv.serve_forever()
    finally:
        srv.server_close()

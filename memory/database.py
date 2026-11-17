"""SQLite persistence — WAL, migrations, atomic claim, handoffs, audit logs, crash-safe recovery."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 5
V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS github_connections (id TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT '', scopes TEXT NOT NULL DEFAULT '', token_ref TEXT NOT NULL DEFAULT 'env:GITHUB_TOKEN', status TEXT NOT NULL DEFAULT 'DISCONNECTED', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_repositories (project_id TEXT PRIMARY KEY REFERENCES projects(id), repo_full_name TEXT NOT NULL, owner TEXT NOT NULL, name TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'private', default_branch TEXT NOT NULL DEFAULT '', working_branch TEXT, connected_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pull_requests (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, repo_full_name TEXT NOT NULL, number INTEGER, title TEXT NOT NULL, head_branch TEXT NOT NULL, base_branch TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN', url TEXT NOT NULL DEFAULT '', task_id TEXT, merged_at REAL, merge_commit_sha TEXT, approval_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS uploaded_artifacts (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'document', size_bytes INTEGER NOT NULL DEFAULT 0, stored_path TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'dashboard', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS synchronization_events (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS request_idempotency (key TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT '', response_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_pr_project ON pull_requests(project_id);
CREATE INDEX IF NOT EXISTS idx_sync_project ON synchronization_events(project_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_uploads_project ON uploaded_artifacts(project_id, created_at DESC);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INPUT_RECEIVED',
    lifecycle TEXT NOT NULL DEFAULT 'IDLE',
    workspace TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    agent_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
    priority INTEGER NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}',
    result TEXT, output_data TEXT NOT NULL DEFAULT '{}',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    error TEXT, retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3, locked_by TEXT, locked_at REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on)
);
CREATE TABLE IF NOT EXISTS task_handoffs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_task_id TEXT NOT NULL,
    from_agent_id TEXT NOT NULL, summary TEXT NOT NULL,
    artifacts TEXT NOT NULL DEFAULT '[]', data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'IDLE', last_run_at REAL
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL,
    finished_at REAL, duration_ms INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
    agent_id TEXT, project_id TEXT,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT,
    action TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
    requested_at REAL NOT NULL, expires_at REAL, decided_at REAL, decided_by TEXT,
    consumed_at REAL
);
CREATE TABLE IF NOT EXISTS approval_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL,
    project_id TEXT NOT NULL, event TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_memory (
    project_id TEXT PRIMARY KEY, facts TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, label TEXT NOT NULL,
    snapshot_path TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
    path TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_exec_logs ON execution_logs(execution_id);
CREATE INDEX IF NOT EXISTS idx_handoffs_project ON task_handoffs(project_id);
CREATE INDEX IF NOT EXISTS idx_approval_audit ON approval_audit_logs(project_id, approval_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_unique_step ON tasks(project_id, agent_id, title);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self.migrate()

    # ---- schema ----
    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.executescript(V4_SCHEMA)  # v4: additive, preserves all v3 data
            if "description" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(projects)")}:
                self._conn.execute("ALTER TABLE projects ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            proj_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "lifecycle" not in proj_cols:
                self._conn.execute("ALTER TABLE projects ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'IDLE'")

            task_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "output_data" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN output_data TEXT NOT NULL DEFAULT '{}'")
            if "acceptance_criteria" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN acceptance_criteria TEXT NOT NULL DEFAULT '[]'")

            appr_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(approvals)").fetchall()}
            if "expires_at" not in appr_cols:
                self._conn.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
            if "consumed_at" not in appr_cols:
                self._conn.execute("ALTER TABLE approvals ADD COLUMN consumed_at REAL")

            usage_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(model_usage)").fetchall()}
            if "project_id" not in usage_cols:
                self._conn.execute("ALTER TABLE model_usage ADD COLUMN project_id TEXT")

            pr_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(pull_requests)").fetchall()}
            if "merged_at" not in pr_cols:
                self._conn.execute("ALTER TABLE pull_requests ADD COLUMN merged_at REAL")
            if "merge_commit_sha" not in pr_cols:
                self._conn.execute("ALTER TABLE pull_requests ADD COLUMN merge_commit_sha TEXT")
            if "approval_id" not in pr_cols:
                self._conn.execute("ALTER TABLE pull_requests ADD COLUMN approval_id TEXT")

            up_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(uploaded_artifacts)").fetchall()}
            if "category" not in up_cols:
                self._conn.execute("ALTER TABLE uploaded_artifacts ADD COLUMN category TEXT NOT NULL DEFAULT 'document'")
            if "size_bytes" not in up_cols:
                self._conn.execute("ALTER TABLE uploaded_artifacts ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0")
            if "stored_path" not in up_cols:
                self._conn.execute("ALTER TABLE uploaded_artifacts ADD COLUMN stored_path TEXT NOT NULL DEFAULT ''")
            if "summary" not in up_cols:
                self._conn.execute("ALTER TABLE uploaded_artifacts ADD COLUMN summary TEXT NOT NULL DEFAULT ''")

            row = self._conn.execute("SELECT version FROM migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone()
            if not row:
                self._conn.execute("INSERT INTO migrations(version, applied_at) VALUES(?,?)",
                                   (SCHEMA_VERSION, time.time()))
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- projects ----
    def create_project(self, name: str, workspace: str, metadata: dict | None = None) -> str:
        pid = f"proj_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.execute("INSERT INTO projects(id,name,status,lifecycle,workspace,created_at,updated_at,metadata) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     (pid, name, "INPUT_RECEIVED", "INPUT_RECEIVED", workspace, now, now,
                      json.dumps(metadata or {})))
        return pid

    def update_project_metadata(self, pid: str, updates: dict) -> None:
        with self._lock:
            row = self._conn.execute("SELECT metadata FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                return
            try:
                meta = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            meta.update(updates)
            self._conn.execute("UPDATE projects SET metadata=?, updated_at=? WHERE id=?",
                               (json.dumps(meta), time.time(), pid))
            self._conn.commit()

    def set_project_status(self, pid: str, status: str, lifecycle: str | None = None) -> None:
        if lifecycle:
            self.execute("UPDATE projects SET status=?, lifecycle=?, updated_at=? WHERE id=?",
                         (status, lifecycle, time.time(), pid))
        else:
            self.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, time.time(), pid))

    def get_project(self, pid: str) -> dict | None:
        return self.query_one("SELECT * FROM projects WHERE id=?", (pid,))

    def list_projects(self) -> list[dict]:
        return self.query("SELECT * FROM projects ORDER BY created_at DESC")

    # ---- tasks & dependencies ----
    def create_task(self, project_id: str, agent_id: str, title: str, payload: dict | None = None,
                    priority: int = 100, depends_on: list[str] | None = None,
                    max_retries: int = 3,
                    acceptance_criteria: list[str] | None = None) -> str:
        """Idempotent per (project, agent, title): re-planning never duplicates work."""
        existing = self.query_one(
            "SELECT id FROM tasks WHERE project_id=? AND agent_id=? AND title=?", (project_id, agent_id, title))
        if existing:
            tid = existing["id"]
            for dep in (depends_on or []):
                if dep and dep != tid:
                    self.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)",
                                 (tid, dep))
            return tid
        tid = f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.execute(
            "INSERT INTO tasks(id,project_id,agent_id,title,status,priority,payload,"
            "acceptance_criteria,max_retries,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, project_id, agent_id, title, "QUEUED", priority,
             json.dumps(payload or {}), json.dumps(acceptance_criteria or []),
             max_retries, now, now))
        for dep in (depends_on or []):
            if dep and dep != tid:
                self.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)",
                             (tid, dep))
        return tid

    def update_task_payload(self, tid: str, payload: dict) -> None:
        self.execute("UPDATE tasks SET payload=?, updated_at=? WHERE id=?",
                     (json.dumps(payload), time.time(), tid))

    def get_task(self, tid: str) -> dict | None:
        return self.query_one("SELECT * FROM tasks WHERE id=?", (tid,))

    def get_task_dependencies(self, tid: str) -> list[dict]:
        return self.query("""
            SELECT p.* FROM task_dependencies d
            JOIN tasks p ON p.id = d.depends_on
            WHERE d.task_id = ?
            ORDER BY p.priority ASC, p.created_at ASC
        """, (tid,))

    def claim_task(self, worker_id: str, stale_after: float = 0.0) -> dict | None:
        """Atomically claim the next runnable task (reclaiming stale locks when configured)."""
        with self._lock:
            now = time.time()
            if stale_after and stale_after > 0:
                cutoff = now - float(stale_after)
                self._conn.execute(
                    "UPDATE executions SET status='INTERRUPTED', finished_at=?, error='Stale worker lock reclaimed' "
                    "WHERE status='RUNNING' AND task_id IN "
                    "(SELECT id FROM tasks WHERE status='EXECUTING' AND locked_at IS NOT NULL AND locked_at < ?)",
                    (now, cutoff))
                self._conn.execute(
                    "UPDATE tasks SET status='QUEUED', locked_by=NULL, locked_at=NULL, updated_at=? "
                    "WHERE status='EXECUTING' AND locked_at IS NOT NULL AND locked_at < ?",
                    (now, cutoff))
            row = self._conn.execute("""
                SELECT t.* FROM tasks t
                JOIN projects pr ON pr.id = t.project_id
                WHERE t.status='QUEUED'
                  AND pr.lifecycle NOT IN ('PAUSED', 'CANCELLED', 'WAITING_FOR_APPROVAL')
                  AND NOT EXISTS (
                    SELECT 1 FROM task_dependencies d JOIN tasks p ON p.id=d.depends_on
                    WHERE d.task_id=t.id AND p.status NOT IN ('COMPLETED','SKIPPED')
                ) ORDER BY t.priority ASC, t.created_at ASC LIMIT 1
            """).fetchone()
            if not row:
                self._conn.commit()
                return None
            cur = self._conn.execute(
                "UPDATE tasks SET status='EXECUTING', locked_by=?, locked_at=?, updated_at=? "
                "WHERE id=? AND status='QUEUED'", (worker_id, now, now, row["id"]))
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            task = dict(row)
            task["status"] = "EXECUTING"
            return task

    def set_task_status(self, tid: str, status: str, result: str | None = None,
                        error: str | None = None, output_data: dict | None = None) -> None:
        out_json = json.dumps(output_data) if output_data is not None else None
        if status in ("COMPLETED", "FAILED", "CANCELLED", "SKIPPED"):
            self.execute(
                "UPDATE tasks SET status=?, result=COALESCE(?,result), "
                "output_data=COALESCE(?,output_data), error=COALESCE(?,error), "
                "locked_by=NULL, locked_at=NULL, updated_at=? WHERE id=?",
                (status, result, out_json, error, time.time(), tid))
        else:
            self.execute(
                "UPDATE tasks SET status=?, result=COALESCE(?,result), "
                "output_data=COALESCE(?,output_data), error=COALESCE(?,error), "
                "updated_at=? WHERE id=?",
                (status, result, out_json, error, time.time(), tid))

    def propagate_dependency_failures(self, project_id: str) -> int:
        """Mark QUEUED tasks as SKIPPED when any upstream dependency has permanently FAILED."""
        with self._lock:
            total_skipped = 0
            while True:
                rows = self._conn.execute("""
                    SELECT DISTINCT t.id, p.agent_id AS failed_agent
                    FROM tasks t
                    JOIN task_dependencies d ON d.task_id = t.id
                    JOIN tasks p ON p.id = d.depends_on
                    WHERE t.project_id = ?
                      AND t.status = 'QUEUED'
                      AND (
                          p.status = 'FAILED'
                          OR (p.status = 'SKIPPED' AND COALESCE(p.error, '') LIKE 'Blocked by failed dependency%')
                      )
                """, (project_id,)).fetchall()
                if not rows:
                    break
                now = time.time()
                for r in rows:
                    self._conn.execute(
                        "UPDATE tasks SET status='SKIPPED', error=?, locked_by=NULL, locked_at=NULL, updated_at=? "
                        "WHERE id=? AND status='QUEUED'",
                        (f"Blocked by failed dependency ({r['failed_agent']})", now, r["id"]))
                    total_skipped += 1
            if total_skipped:
                self._conn.commit()
            return total_skipped

    def increment_retry(self, tid: str) -> int:
        row = self.query_one("SELECT retries FROM tasks WHERE id=?", (tid,))
        n = (row["retries"] if row else 0) + 1
        self.execute("UPDATE tasks SET retries=?, updated_at=? WHERE id=?", (n, time.time(), tid))
        return n

    def list_tasks(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM tasks WHERE project_id=? ORDER BY priority, created_at", (project_id,))

    def task_dependencies(self, task_id: str) -> list[str]:
        rows = self.query("SELECT depends_on FROM task_dependencies WHERE task_id=?", (task_id,))
        return [r["depends_on"] for r in rows]

    def requeue_failed(self, project_id: str) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='QUEUED', error=NULL, updated_at=? "
                "WHERE project_id=? AND status='FAILED' AND retries < max_retries",
                (now, project_id))
            requeued = cur.rowcount
            if requeued > 0:
                # Also restore downstream tasks that were skipped strictly due to upstream failure
                self._conn.execute(
                    "UPDATE tasks SET status='QUEUED', error=NULL, updated_at=? "
                    "WHERE project_id=? AND status='SKIPPED' "
                    "AND COALESCE(error, '') LIKE 'Blocked by failed dependency%'",
                    (now, project_id))
            self._conn.commit()
            return requeued

    def detect_interrupted(self) -> list[dict]:
        return self.query("SELECT * FROM tasks WHERE status='EXECUTING'")

    def recover_interrupted(self) -> int:
        """Crash recovery: requeue EXECUTING tasks and fail their dangling executions."""
        with self._lock:
            now = time.time()
            self._conn.execute(
                "UPDATE executions SET status='INTERRUPTED', finished_at=? "
                "WHERE status='RUNNING' AND task_id IN (SELECT id FROM tasks WHERE status='EXECUTING')",
                (now,))
            cur = self._conn.execute(
                "UPDATE tasks SET status='QUEUED', locked_by=NULL, locked_at=NULL, updated_at=? "
                "WHERE status='EXECUTING'", (now,))
            self._conn.commit()
            return cur.rowcount

    # ---- handoffs ----
    def record_handoff(self, project_id: str, from_task_id: str, from_agent_id: str,
                       summary: str, artifacts: list[str] | None = None,
                       data: dict | None = None) -> str:
        hid = f"hoff_{uuid.uuid4().hex[:12]}"
        self.execute(
            "INSERT INTO task_handoffs(id,project_id,from_task_id,from_agent_id,summary,artifacts,data,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (hid, project_id, from_task_id, from_agent_id, summary[:2000],
             json.dumps(artifacts or []), json.dumps(data or {}), time.time()))
        return hid

    def list_handoffs(self, project_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM task_handoffs WHERE project_id=? ORDER BY created_at ASC",
                          (project_id,))
        for r in rows:
            try:
                r["artifacts"] = json.loads(r.get("artifacts") or "[]")
            except json.JSONDecodeError:
                r["artifacts"] = []
            try:
                r["data"] = json.loads(r.get("data") or "{}")
            except json.JSONDecodeError:
                r["data"] = {}
        return rows

    # ---- agents ----
    def update_agent_status(self, agent_id: str, name: str, status: str, description: str = "") -> None:
        now = time.time()
        self.execute(
            "INSERT INTO agents(id,name,description,status,last_run_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, last_run_at=excluded.last_run_at",
            (agent_id, name, description, status, now))

    # ---- executions / logs ----
    def start_execution(self, project_id: str, task_id: str, agent_id: str) -> str:
        eid = f"exec_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO executions(id,project_id,task_id,agent_id,status,started_at) "
                     "VALUES(?,?,?,?,?,?)", (eid, project_id, task_id, agent_id, "RUNNING", time.time()))
        return eid

    def finish_execution(self, eid: str, status: str, error: str | None = None) -> None:
        row = self.query_one("SELECT started_at FROM executions WHERE id=?", (eid,))
        dur = int((time.time() - row["started_at"]) * 1000) if row else 0
        self.execute("UPDATE executions SET status=?, finished_at=?, duration_ms=?, error=? WHERE id=?",
                     (status, time.time(), dur, error, eid))

    def list_executions(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM executions WHERE project_id=? ORDER BY started_at ASC",
                          (project_id,))

    def log(self, eid: str, level: str, message: str) -> None:
        from security.secret_manager import redact
        self.execute("INSERT INTO execution_logs(execution_id,level,message,ts) VALUES(?,?,?,?)",
                     (eid, level, redact(str(message))[:8000], time.time()))

    def get_logs(self, eid: str) -> list[dict]:
        return self.query("SELECT * FROM execution_logs WHERE execution_id=? ORDER BY ts", (eid,))

    def recent_logs(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM execution_logs ORDER BY id DESC LIMIT ?", (limit,))

    # ---- approvals & audit logs ----
    def create_approval(self, project_id: str, action: str, detail: str,
                        task_id: str | None = None, ttl_seconds: int = 3600) -> str:
        from security.secret_manager import redact
        aid = f"appr_{uuid.uuid4().hex[:12]}"
        now = time.time()
        expires = now + int(ttl_seconds) if (ttl_seconds is not None and int(ttl_seconds) != 0) else None
        clean_detail = redact(str(detail))[:2000]
        self.execute(
            "INSERT INTO approvals(id,project_id,task_id,action,detail,status,requested_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (aid, project_id, task_id, action, clean_detail, "PENDING", now, expires))
        self.log_approval_audit(aid, project_id, "REQUESTED", actor="system", detail=f"{action}: {clean_detail[:200]}")
        return aid

    def expire_approvals(self, now: float | None = None) -> int:
        ts = now if now is not None else time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, project_id FROM approvals "
                "WHERE status='PENDING' AND expires_at IS NOT NULL AND expires_at <= ?",
                (ts,)).fetchall()
            if not rows:
                return 0
            for r in rows:
                self._conn.execute(
                    "UPDATE approvals SET status='EXPIRED', decided_at=?, decided_by='ttl' "
                    "WHERE id=? AND status='PENDING'", (ts, r["id"]))
                self._conn.execute(
                    "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (r["id"], r["project_id"], "EXPIRED", "system", "Approval TTL elapsed", ts))
            self._conn.commit()
            return len(rows)

    def decide_approval(self, aid: str, approved: bool, by: str = "") -> bool:
        self.expire_approvals()
        with self._lock:
            row = self._conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
            now = time.time()
            if not row:
                self._conn.execute(
                    "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (aid, "", "UNKNOWN_APPROVAL", str(by), "Decision on non-existent id", now))
                self._conn.commit()
                return False
            if row["status"] != "PENDING":
                self._conn.execute(
                    "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (aid, row["project_id"], f"DUPLICATE_OR_{row['status']}", str(by),
                     f"Attempted {'approve' if approved else 'reject'} on {row['status']}", now))
                self._conn.commit()
                return False
            new_status = "APPROVED" if approved else "REJECTED"
            cur = self._conn.execute(
                "UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='PENDING'",
                (new_status, now, str(by), aid))
            if cur.rowcount > 0:
                self._conn.execute(
                    "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (aid, row["project_id"], new_status, str(by), row["action"], now))
            self._conn.commit()
            return cur.rowcount > 0

    def mark_approval_consumed(self, aid: str, actor: str = "execution") -> bool:
        now = time.time()
        with self._lock:
            row = self._conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
            if not row:
                return False
            cur = self._conn.execute(
                "UPDATE approvals SET consumed_at=? WHERE id=? AND status='APPROVED' "
                "AND consumed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
                (now, aid, now))
            if cur.rowcount != 1:
                return False
            self._conn.execute(
                "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
                "VALUES(?,?,?,?,?,?)",
                (aid, row["project_id"], "CONSUMED", actor, row["action"], now))
            self._conn.commit()
            return True

    def log_approval_audit(self, approval_id: str, project_id: str, event: str,
                           actor: str = "", detail: str = "") -> None:
        from security.secret_manager import redact
        self.execute(
            "INSERT INTO approval_audit_logs(approval_id,project_id,event,actor,detail,ts) "
            "VALUES(?,?,?,?,?,?)",
            (approval_id, project_id, event, str(actor), redact(str(detail))[:1000], time.time()))

    def list_approval_audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[dict]:
        if project_id:
            return self.query(
                "SELECT * FROM approval_audit_logs WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, limit))
        return self.query("SELECT * FROM approval_audit_logs ORDER BY id DESC LIMIT ?", (limit,))

    def pending_approvals(self, project_id: str | None = None) -> list[dict]:
        self.expire_approvals()
        if project_id:
            return self.query("SELECT * FROM approvals WHERE project_id=? AND status='PENDING'", (project_id,))
        return self.query("SELECT * FROM approvals WHERE status='PENDING'")

    def get_approval(self, aid: str) -> dict | None:
        self.expire_approvals()
        return self.query_one("SELECT * FROM approvals WHERE id=?", (aid,))

    # ---- memory / checkpoints / artifacts / usage ----
    def set_memory(self, project_id: str, facts: dict) -> None:
        self.execute("INSERT INTO project_memory(project_id,facts,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(project_id) DO UPDATE SET facts=excluded.facts, updated_at=excluded.updated_at",
                     (project_id, json.dumps(facts), time.time()))

    def get_memory(self, project_id: str) -> dict:
        row = self.query_one("SELECT facts FROM project_memory WHERE project_id=?", (project_id,))
        return json.loads(row["facts"]) if row else {}

    def record_checkpoint(self, project_id: str, label: str, snapshot_path: str) -> str:
        cid = f"chk_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO checkpoints(id,project_id,label,snapshot_path,created_at) VALUES(?,?,?,?,?)",
                     (cid, project_id, label[:200], snapshot_path, time.time()))
        return cid

    def list_checkpoints(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM checkpoints WHERE project_id=? ORDER BY created_at DESC",
                          (project_id,))

    def add_artifact(self, project_id: str, kind: str, path: str, sha256: str = "") -> str:
        aid = f"art_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO artifacts(id,project_id,kind,path,sha256,created_at) VALUES(?,?,?,?,?,?)",
                     (aid, project_id, kind, path, sha256, time.time()))
        return aid

    def list_artifacts(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC",
                          (project_id,))

    def record_usage(self, provider: str, model: str, agent_id: str, pt: int = 0,
                     ct: int = 0, cost: float = 0.0, project_id: str | None = None) -> None:
        self.execute("INSERT INTO model_usage(provider,model,agent_id,project_id,prompt_tokens,"
                     "completion_tokens,cost_usd,ts) VALUES(?,?,?,?,?,?,?,?)",
                     (provider, model, agent_id, project_id, pt, ct, cost, time.time()))

    def usage_summary(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            return self.query(
                "SELECT provider, model, COUNT(*) runs, SUM(prompt_tokens) pt, "
                "SUM(completion_tokens) ct, SUM(cost_usd) cost FROM model_usage "
                "WHERE project_id=? GROUP BY provider, model ORDER BY runs DESC",
                (project_id,))
        return self.query("SELECT provider, model, COUNT(*) runs, SUM(prompt_tokens) pt, "
                          "SUM(completion_tokens) ct, SUM(cost_usd) cost FROM model_usage "
                          "GROUP BY provider, model ORDER BY runs DESC")

    def total_cost_usd(self, project_id: str | None = None) -> float:
        if project_id:
            row = self.query_one("SELECT COALESCE(SUM(cost_usd), 0.0) c FROM model_usage WHERE project_id=?",
                                 (project_id,))
        else:
            row = self.query_one("SELECT COALESCE(SUM(cost_usd), 0.0) c FROM model_usage")
        return float((row or {}).get("c") or 0.0)

    def backup(self, dest: str) -> str:
        """Consistent online backup (safe while writers are active)."""
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(dest)
            try:
                self._conn.backup(target)
            finally:
                target.close()
        return dest

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---- v4/v5: multi-project GitHub bindings, PRs, uploads, sync & idempotency ----
    def bind_repository(self, project_id: str, repo_full_name: str, visibility: str = "private",
                        default_branch: str = "", working_branch: str | None = None) -> dict | None:
        from security.validation import validate_branch_name, validate_repo_full_name
        clean_repo = (repo_full_name or "").strip()
        ok, err = validate_repo_full_name(clean_repo)
        if not ok:
            raise ValueError(err)
        parts = clean_repo.split("/")
        clean_default = (default_branch or "").strip()
        if clean_default:
            ok_b, err_b = validate_branch_name(clean_default)
            if not ok_b:
                raise ValueError(err_b)
        clean_working = (working_branch or "").strip() or (clean_default or None)
        if clean_working:
            ok_w, err_w = validate_branch_name(clean_working)
            if not ok_w:
                raise ValueError(err_w)
        vis = visibility if visibility in ("private", "public", "internal") else "private"
        self.execute(
            "INSERT INTO project_repositories(project_id,repo_full_name,owner,name,visibility,"
            "default_branch,working_branch,connected_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET repo_full_name=excluded.repo_full_name,"
            "owner=excluded.owner,name=excluded.name,visibility=excluded.visibility,"
            "default_branch=excluded.default_branch,working_branch=excluded.working_branch",
            (project_id, clean_repo, parts[0], parts[1], vis, clean_default, clean_working, time.time()),
        )
        self.record_sync(project_id, "system", "repo_bound",
                         f"{clean_repo} (default={clean_default or 'pending'}, working={clean_working or 'pending'})")
        return self.get_repository(project_id)

    def set_working_branch(self, project_id: str, working_branch: str,
                           default_branch: str | None = None, source: str = "dashboard") -> dict | None:
        from security.validation import validate_branch_name
        repo = self.get_repository(project_id)
        if not repo:
            raise ValueError("No GitHub repository connected to this project")
        ok, err = validate_branch_name(working_branch)
        if not ok:
            raise ValueError(err)
        clean_w = working_branch.strip()
        if default_branch is not None and default_branch.strip():
            ok_d, err_d = validate_branch_name(default_branch)
            if not ok_d:
                raise ValueError(err_d)
            self.execute(
                "UPDATE project_repositories SET working_branch=?, default_branch=? WHERE project_id=?",
                (clean_w, default_branch.strip(), project_id),
            )
        else:
            self.execute(
                "UPDATE project_repositories SET working_branch=? WHERE project_id=?",
                (clean_w, project_id),
            )
        self.record_sync(project_id, source, "branch_selected", clean_w)
        return self.get_repository(project_id)

    def get_repository(self, project_id: str) -> dict | None:
        return self.query_one("SELECT * FROM project_repositories WHERE project_id=?", (project_id,))

    def assert_repo(self, project_id: str, repo_full_name: str) -> dict:
        b = self.get_repository(project_id)
        if not b:
            raise PermissionError("No GitHub repository connected to this project")
        if b["repo_full_name"] != repo_full_name:
            raise PermissionError(f"Repository isolation violation: project bound to {b['repo_full_name']}")
        return b

    def record_pull_request(self, project_id: str, repo: str, number: int | None, title: str,
                            head: str, base: str, url: str = "", task_id: str | None = None,
                            state: str = "OPEN") -> str:
        now = time.time()
        norm_state = (state or "OPEN").upper()
        if number is not None:
            existing = self.query_one(
                "SELECT id FROM pull_requests WHERE project_id=? AND repo_full_name=? AND number=?",
                (project_id, repo, int(number)),
            )
            if existing:
                self.execute(
                    "UPDATE pull_requests SET title=?, head_branch=?, base_branch=?, state=?, "
                    "url=CASE WHEN ?!='' THEN ? ELSE url END, updated_at=? WHERE id=?",
                    (title, head, base, norm_state, url, url, now, existing["id"]),
                )
                return existing["id"]
        pr_id = f"pr_{uuid.uuid4().hex[:12]}"
        self.execute(
            "INSERT INTO pull_requests(id,project_id,repo_full_name,number,title,head_branch,"
            "base_branch,state,url,task_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (pr_id, project_id, repo, number, title, head, base, norm_state, url, task_id, now, now),
        )
        return pr_id

    def update_pull_request_state(self, project_id: str, number: int, state: str, *,
                                  merge_commit_sha: str | None = None,
                                  approval_id: str | None = None) -> None:
        now = time.time()
        norm = (state or "OPEN").upper()
        merged_at = now if norm == "MERGED" else None
        self.execute(
            "UPDATE pull_requests SET state=?, merged_at=COALESCE(?, merged_at), "
            "merge_commit_sha=COALESCE(?, merge_commit_sha), approval_id=COALESCE(?, approval_id), "
            "updated_at=? WHERE project_id=? AND number=?",
            (norm, merged_at, merge_commit_sha, approval_id, now, project_id, int(number)),
        )

    def list_pull_requests(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM pull_requests WHERE project_id=? ORDER BY created_at DESC", (project_id,))

    def record_uploaded_artifact(self, project_id: str, filename: str, sha256: str = "",
                                 source: str = "dashboard", category: str = "document",
                                 size_bytes: int = 0, stored_path: str = "",
                                 summary: str = "") -> str:
        from security.secret_manager import redact
        uid = f"upl_{uuid.uuid4().hex[:12]}"
        self.execute(
            "INSERT INTO uploaded_artifacts(id,project_id,filename,category,size_bytes,stored_path,"
            "summary,sha256,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, project_id, Path(filename).name[:255], category[:40], int(size_bytes or 0),
             stored_path[:500], redact(summary)[:1000], sha256, source[:40], time.time()),
        )
        return uid

    def list_uploaded_artifacts(self, project_id: str, limit: int = 100) -> list[dict]:
        return self.query(
            "SELECT * FROM uploaded_artifacts WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, int(limit)),
        )

    def list_approvals(self, project_id: str | None = None, status: str | None = None,
                       limit: int = 100) -> list[dict]:
        self.expire_approvals()
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if status:
            clauses.append("status=?")
            params.append(status.upper())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        return self.query(f"SELECT * FROM approvals {where} ORDER BY requested_at DESC LIMIT ?", tuple(params))

    def list_project_logs(self, project_id: str | None = None, *, task_id: str | None = None,
                          execution_id: str | None = None, limit: int = 100) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("e.project_id=?")
            params.append(project_id)
        if task_id:
            clauses.append("e.task_id=?")
            params.append(task_id)
        if execution_id:
            clauses.append("l.execution_id=?")
            params.append(execution_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(500, int(limit or 100))))
        rows = self.query(
            f"SELECT l.id, l.execution_id, e.project_id, e.task_id, e.agent_id, "
            f"l.level, l.message, l.ts FROM execution_logs l "
            f"JOIN executions e ON e.id = l.execution_id "
            f"{where} ORDER BY l.id DESC LIMIT ?",
            tuple(params),
        )
        return list(reversed(rows))

    def record_sync(self, project_id: str, source: str, event: str, detail: str = "") -> None:
        from security.secret_manager import redact
        self.execute(
            "INSERT INTO synchronization_events(project_id,source,event,detail,ts) VALUES(?,?,?,?,?)",
            (project_id or "", source[:40], event[:80], redact(str(detail))[:1000], time.time()),
        )

    def list_sync_events(self, project_id: str | None = None, limit: int = 100) -> list[dict]:
        lim = max(1, min(500, int(limit or 100)))
        if project_id:
            return self.query(
                "SELECT * FROM synchronization_events WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, lim),
            )
        return self.query("SELECT * FROM synchronization_events ORDER BY id DESC LIMIT ?", (lim,))

    def get_idempotent_response(self, key: str, ttl_seconds: int = 600) -> dict | None:
        if not key:
            return None
        row = self.query_one("SELECT response_json, created_at FROM request_idempotency WHERE key=?", (key[:160],))
        if not row:
            return None
        if time.time() - float(row["created_at"]) > ttl_seconds:
            return None
        try:
            return json.loads(row["response_json"])
        except Exception:
            return None

    def save_idempotent_response(self, key: str, project_id: str, response: dict) -> None:
        if not key:
            return
        self.execute(
            "INSERT OR REPLACE INTO request_idempotency(key,project_id,response_json,created_at) VALUES(?,?,?,?)",
            (key[:160], project_id or "", json.dumps(response), time.time()),
        )

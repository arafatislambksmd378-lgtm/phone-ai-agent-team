"""SQLite persistence layer — stdlib only, WAL mode, migrations."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'INPUT_RECEIVED',
    workspace TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    agent_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
    priority INTEGER NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}',
    result TEXT, error TEXT, retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3, locked_by TEXT, locked_at REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on)
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
    agent_id TEXT, prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT,
    action TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
    requested_at REAL NOT NULL, decided_at REAL, decided_by TEXT
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
"""

class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            row = self._conn.execute("SELECT version FROM migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone()
            if not row:
                self._conn.execute("INSERT INTO migrations(version, applied_at) VALUES(?,?)", (SCHEMA_VERSION, time.time()))
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- Projects ----
    def create_project(self, name: str, workspace: str, metadata: dict | None = None) -> str:
        pid = f"proj_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.execute("INSERT INTO projects(id,name,status,workspace,created_at,updated_at,metadata) VALUES(?,?,?,?,?,?,?)",
                     (pid, name, "INPUT_RECEIVED", workspace, now, now, json.dumps(metadata or {})))
        return pid

    def set_project_status(self, pid: str, status: str) -> None:
        self.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, time.time(), pid))

    def get_project(self, pid: str) -> dict | None:
        return self.query_one("SELECT * FROM projects WHERE id=?", (pid,))

    def list_projects(self) -> list[dict]:
        return self.query("SELECT * FROM projects ORDER BY created_at DESC")

    # ---- Tasks ----
    def create_task(self, project_id: str, agent_id: str, title: str, payload: dict | None = None,
                    priority: int = 100, depends_on: list[str] | None = None) -> str:
        tid = f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.execute("INSERT INTO tasks(id,project_id,agent_id,title,status,priority,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tid, project_id, agent_id, title, "QUEUED", priority, json.dumps(payload or {}), now, now))
        for dep in (depends_on or []):
            self.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)", (tid, dep))
        return tid

    def claim_task(self, worker_id: str) -> dict | None:
        """Atomic claim: pick highest-priority QUEUED task whose deps are COMPLETED."""
        with self._lock:
            row = self._conn.execute("""
                SELECT t.* FROM tasks t WHERE t.status='QUEUED' AND NOT EXISTS (
                    SELECT 1 FROM task_dependencies d JOIN tasks p ON p.id=d.depends_on
                    WHERE d.task_id=t.id AND p.status NOT IN ('COMPLETED','SKIPPED')
                ) ORDER BY t.priority ASC, t.created_at ASC LIMIT 1
            """).fetchone()
            if not row:
                return None
            now = time.time()
            cur = self._conn.execute("UPDATE tasks SET status='EXECUTING', locked_by=?, locked_at=?, updated_at=? WHERE id=? AND status='QUEUED'",
                                     (worker_id, now, now, row["id"]))
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            return dict(row)

    def set_task_status(self, tid: str, status: str, result: str | None = None, error: str | None = None) -> None:
        self.execute("UPDATE tasks SET status=?, result=COALESCE(?,result), error=COALESCE(?,error), updated_at=? WHERE id=?",
                     (status, result, error, time.time(), tid))

    def increment_retry(self, tid: str) -> int:
        row = self.query_one("SELECT retries FROM tasks WHERE id=?", (tid,))
        n = (row["retries"] if row else 0) + 1
        self.execute("UPDATE tasks SET retries=?, updated_at=? WHERE id=?", (n, time.time(), tid))
        return n

    def list_tasks(self, project_id: str) -> list[dict]:
        return self.query("SELECT * FROM tasks WHERE project_id=? ORDER BY priority, created_at", (project_id,))

    def recover_interrupted(self) -> int:
        """Reset EXECUTING tasks (crash recovery) back to QUEUED."""
        cur = self.execute("UPDATE tasks SET status='QUEUED', locked_by=NULL, locked_at=NULL, updated_at=? WHERE status='EXECUTING'", (time.time(),))
        return cur.rowcount

    # ---- Executions / logs ----
    def start_execution(self, project_id: str, task_id: str, agent_id: str) -> str:
        eid = f"exec_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO executions(id,project_id,task_id,agent_id,status,started_at) VALUES(?,?,?,?,?,?)",
                     (eid, project_id, task_id, agent_id, "RUNNING", time.time()))
        return eid

    def finish_execution(self, eid: str, status: str, error: str | None = None) -> None:
        row = self.query_one("SELECT started_at FROM executions WHERE id=?", (eid,))
        dur = int((time.time() - row["started_at"]) * 1000) if row else 0
        self.execute("UPDATE executions SET status=?, finished_at=?, duration_ms=?, error=? WHERE id=?",
                     (status, time.time(), dur, error, eid))

    def log(self, eid: str, level: str, message: str) -> None:
        self.execute("INSERT INTO execution_logs(execution_id,level,message,ts) VALUES(?,?,?,?)", (eid, level, message, time.time()))

    def get_logs(self, eid: str) -> list[dict]:
        return self.query("SELECT * FROM execution_logs WHERE execution_id=? ORDER BY ts", (eid,))

    # ---- Approvals ----
    def create_approval(self, project_id: str, action: str, detail: str, task_id: str | None = None) -> str:
        aid = f"appr_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO approvals(id,project_id,task_id,action,detail,status,requested_at) VALUES(?,?,?,?,?,?,?)",
                     (aid, project_id, task_id, action, detail, "PENDING", time.time()))
        return aid

    def decide_approval(self, aid: str, approved: bool, by: str = "") -> None:
        self.execute("UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=?",
                     ("APPROVED" if approved else "REJECTED", time.time(), by, aid))

    def pending_approvals(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            return self.query("SELECT * FROM approvals WHERE project_id=? AND status='PENDING'", (project_id,))
        return self.query("SELECT * FROM approvals WHERE status='PENDING'")

    # ---- Memory / artifacts / usage ----
    def set_memory(self, project_id: str, facts: dict) -> None:
        self.execute("INSERT INTO project_memory(project_id,facts,updated_at) VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET facts=excluded.facts, updated_at=excluded.updated_at",
                     (project_id, json.dumps(facts), time.time()))

    def get_memory(self, project_id: str) -> dict:
        row = self.query_one("SELECT facts FROM project_memory WHERE project_id=?", (project_id,))
        return json.loads(row["facts"]) if row else {}

    def add_artifact(self, project_id: str, kind: str, path: str, sha256: str = "") -> str:
        aid = f"art_{uuid.uuid4().hex[:12]}"
        self.execute("INSERT INTO artifacts(id,project_id,kind,path,sha256,created_at) VALUES(?,?,?,?,?,?)",
                     (aid, project_id, kind, path, sha256, time.time()))
        return aid

    def record_usage(self, provider: str, model: str, agent_id: str, pt: int = 0, ct: int = 0, cost: float = 0.0) -> None:
        self.execute("INSERT INTO model_usage(provider,model,agent_id,prompt_tokens,completion_tokens,cost_usd,ts) VALUES(?,?,?,?,?,?,?)",
                     (provider, model, agent_id, pt, ct, cost, time.time()))

    def backup(self, dest: str) -> str:
        import shutil
        shutil.copy2(str(self.path), dest)
        return dest

    def close(self) -> None:
        with self._lock:
            self._conn.close()

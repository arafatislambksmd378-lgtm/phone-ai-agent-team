"""Orchestrator engine — planner + scheduler + agents + lifecycle, crash-safe."""
from __future__ import annotations
import logging
import time
from pathlib import Path

from agents.registry import AgentRegistry
from config.settings import Settings
from execution.file_manager import FileManager
from execution.sandbox import Sandbox
from integrations.ai_providers.router import ModelRouter
from memory.database import Database
from memory.project_memory import ProjectMemory
from orchestrator.lifecycle import LifecycleManager
from orchestrator.planner import TaskPlanner
from orchestrator.scheduler import Scheduler
from security.approval import ApprovalManager
from security.validation import validate_upload, safe_extract_zip

log = logging.getLogger("orchestrator")

FINAL_TASK_STATES = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}


class OrchestratorEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.memory = ProjectMemory(self.db)
        self.lifecycle = LifecycleManager(self.db)
        self.planner = TaskPlanner(self.db)
        self.approvals = ApprovalManager(self.db)
        self.router = ModelRouter(settings, db=self.db)
        self.scheduler = Scheduler(self.db, max_workers=settings.max_concurrent_workers)
        self.scheduler.set_worker(self._execute_task)
        self.scheduler.set_idle_callback(self._maybe_finish_projects)
        self._paused = False
        self._cancelled: set[str] = set()
        self._on_progress = None
        self._finish_lock = __import__("threading").Lock()

    # ---------- hooks ----------
    def set_progress_callback(self, fn) -> None:
        self._on_progress = fn

    def _notify(self, project_id: str, msg: str) -> None:
        from security.secret_manager import redact
        safe = redact(msg)
        log.info("[%s] %s", project_id, safe)
        if self._on_progress:
            try:
                self._on_progress(project_id, safe)
            except Exception:
                log.debug("progress callback failed", exc_info=True)

    # ---------- project intake ----------
    def create_project(self, name: str, requirements: str = "", quick: bool = False) -> str:
        safe_name = "".join(c for c in (name or "project") if c.isalnum() or c in "-_")[:40] or "project"
        ws = str((self.settings.workspace_root / f"{safe_name}_{int(time.time())}_{len(name or '')}").resolve())
        Path(ws).mkdir(parents=True, exist_ok=True)
        pid = self.db.create_project(safe_name, ws, {"requirements_preview": (requirements or "")[:500]})
        self.lifecycle.set(pid, "INPUT_RECEIVED")
        self._notify(pid, f"Project created: {safe_name}")
        FileManager(ws).write("requirements.txt", requirements or f"Project: {name}")
        Sandbox(ws).init_git()
        self.lifecycle.set(pid, "VALIDATING")
        self.lifecycle.set(pid, "PLANNING")
        task_ids = self.planner.plan(pid, requirements, quick=quick)
        self.lifecycle.set(pid, "QUEUED")
        self._notify(pid, f"Planned {len(task_ids)} tasks. Starting workers…")
        self.scheduler.start()
        return pid

    def ingest_upload(self, project_id: str, local_path: str, original_name: str) -> tuple[bool, str]:
        """Validate + stage an uploaded blueprint into the project workspace."""
        proj = self.db.get_project(project_id)
        if not proj:
            return False, "Project not found"
        size = Path(local_path).stat().st_size
        ok, msg = validate_upload(original_name, size, self.settings.max_upload_mb)
        if not ok:
            return False, msg
        from security.validation import validate_upload_content
        ok, msg = validate_upload_content(original_name, local_path)
        if not ok:
            return False, msg
        ws = Path(proj["workspace"])
        if original_name.lower().endswith(".zip"):
            ok, msg, files = safe_extract_zip(
                local_path, str(ws / "extracted"),
                self.settings.max_zip_files, self.settings.max_zip_mb)
            if ok:
                self._append_requirements(project_id, f"Blueprint archive: {original_name} ({len(files)} files)")
            return ok, msg
        dest = ws / "uploads" / Path(original_name).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(local_path).read_bytes())
        text = dest.read_bytes()[:200_000].decode("utf-8", errors="ignore")
        self._append_requirements(project_id, text)
        return True, f"Received {original_name} ({size} bytes)"

    def _append_requirements(self, project_id: str, text: str) -> None:
        proj = self.db.get_project(project_id)
        if not proj:
            return
        fm = FileManager(proj["workspace"])
        existing = fm.read("requirements.txt") if fm.exists("requirements.txt") else ""
        fm.write("requirements.txt", (existing + "\n" + (text or ""))[:400_000])

    # ---------- execution ----------
    def _execute_task(self, task: dict) -> None:
        pid, tid, agent_id = task["project_id"], task["id"], task["agent_id"]
        if pid in self._cancelled:
            self.db.set_task_status(tid, "CANCELLED", error="Project cancelled")
            return
        wait_until = time.time() + 120
        while self._paused and time.time() < wait_until:
            time.sleep(0.5)
            if pid in self._cancelled:
                self.db.set_task_status(tid, "CANCELLED", error="Project cancelled")
                return
        proj = self.db.get_project(pid)
        if not proj:
            self.db.set_task_status(tid, "FAILED", error="Project not found")
            return
        ws = proj["workspace"]
        fm = FileManager(ws)
        registry = AgentRegistry(workspace=ws, ai_router=self.router, db=self.db, file_manager=fm)
        eid = self.db.start_execution(pid, tid, agent_id)
        if self.lifecycle.get(pid) in ("QUEUED", "RECOVERING"):
            self.lifecycle.set(pid, "EXECUTING")
        self._notify(pid, f"▶ {agent_id}: {task['title']}")
        started = time.time()
        try:
            result = registry.run(agent_id, task)
        except Exception as e:  # agent crash must never orphan the task
            log.exception("agent %s crashed", agent_id)
            result = None
            err = f"{type(e).__name__}: {e}"
        else:
            err = ""
        elapsed_ms = int((time.time() - started) * 1000)

        if result is not None and result.ok:
            self.db.set_task_status(tid, "COMPLETED", result=result.summary)
            self.db.finish_execution(eid, "COMPLETED")
            self.db.log(eid, "INFO", f"{agent_id} ok in {elapsed_ms}ms: {result.summary}")
            self._notify(pid, f"✓ {agent_id}: {result.summary}")
            Sandbox(ws).checkpoint(f"{agent_id}: {task['title']}")
            for rel in result.files:
                try:
                    self.db.add_artifact(pid, "file", rel, fm.sha256(rel))
                except Exception:
                    pass
        else:
            reason = err or (result.error if result else "") or (result.summary if result else "unknown error")
            n = self.db.increment_retry(tid)
            max_retries = int(task.get("max_retries") or 3)
            self.db.finish_execution(eid, "FAILED", error=reason)
            self.db.log(eid, "ERROR", f"{agent_id} failed: {reason}")
            if n < max_retries:
                self.db.set_task_status(tid, "QUEUED", error=reason)
                self._notify(pid, f"↻ {agent_id} retry {n}/{max_retries}: {reason[:200]}")
            else:
                self.db.set_task_status(tid, "FAILED", error=reason)
                self._notify(pid, f"✗ {agent_id} failed: {reason[:300]}")
        self._maybe_finish_project(pid)

    def _maybe_finish_projects(self) -> None:
        """When the pool is idle, close out any project whose tasks are all final."""
        if self.scheduler.active_count() > 0:
            return
        for proj in self.db.query("SELECT id FROM projects WHERE status IN "
                                  "('QUEUED','EXECUTING','RECOVERING','TESTING','REVIEWING')"):
            self._maybe_finish_project(proj["id"])

    def _maybe_finish_project(self, pid: str) -> bool:
        if pid in self._cancelled:
            return False
        tasks = self.db.list_tasks(pid)
        if not tasks or not all(t["status"] in FINAL_TASK_STATES for t in tasks):
            return False
        with self._finish_lock:
            state = self.lifecycle.get(pid)
            if state in ("COMPLETED", "CANCELLED"):
                return False
            failed = [t for t in tasks if t["status"] == "FAILED"]
            pending_approvals = self.db.pending_approvals(pid)
            if pending_approvals:
                if state == "QUEUED":
                    self.lifecycle.set(pid, "EXECUTING")
                self.lifecycle.set(pid, "WAITING_FOR_APPROVAL")
                self._notify(pid, f"⏸ {len(pending_approvals)} approval(s) pending.")
                return False
            if failed:
                if state == "QUEUED":
                    self.lifecycle.set(pid, "EXECUTING")
                if self.lifecycle.set(pid, "FAILED"):
                    self._notify(pid, f"Project FAILED ({len(failed)} task(s)). Use /recover to retry.")
            else:
                # Walk the legal path to COMPLETED so the state machine stays honest.
                for intermediate in ("EXECUTING", "TESTING", "REVIEWING"):
                    if self.lifecycle.get(pid) == intermediate:
                        continue
                    self.lifecycle.set(pid, intermediate)
                if self.lifecycle.set(pid, "COMPLETED"):
                    self._notify(pid, "🎉 Project COMPLETED. Workers stopped; Telegram listener stays up.")
            if not self.db.query("SELECT id FROM projects WHERE status IN "
                                 "('QUEUED','EXECUTING','RECOVERING','TESTING','REVIEWING')"):
                self.scheduler.stop()
        return True

    # ---------- control ----------
    def status(self, project_id: str) -> dict:
        proj = self.db.get_project(project_id)
        tasks = self.db.list_tasks(project_id) if proj else []
        return {
            "project": proj,
            "lifecycle": self.lifecycle.get(project_id) if proj else "UNKNOWN",
            "tasks": [{"id": t["id"], "agent": t["agent_id"], "title": t["title"],
                       "status": t["status"], "error": t["error"], "retries": t["retries"]}
                      for t in tasks],
            "pending_approvals": self.db.pending_approvals(project_id) if proj else [],
        }

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def cancel(self, project_id: str) -> None:
        self._cancelled.add(project_id)
        for t in self.db.list_tasks(project_id):
            if t["status"] in ("QUEUED", "EXECUTING"):
                self.db.set_task_status(t["id"], "CANCELLED", error="Project cancelled")
        self.lifecycle.set(project_id, "CANCELLED")
        self.db.set_project_status(project_id, "CANCELLED", lifecycle="CANCELLED")

    def recover(self, project_id: str) -> dict:
        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "error": "Project not found"}
        interrupted = self.db.recover_interrupted()
        requeued = self.db.requeue_failed(project_id)
        if self.lifecycle.get(project_id) in ("FAILED", "PAUSED"):
            self.lifecycle.set(project_id, "RECOVERING")
            self.lifecycle.set(project_id, "QUEUED")
        elif self.lifecycle.get(project_id) == "RECOVERING":
            self.lifecycle.set(project_id, "QUEUED")
        self.scheduler.start()
        self._notify(project_id, f"♻️ Recovered: {interrupted} interrupted, {requeued} failed requeued.")
        return {"ok": True, "interrupted": interrupted, "requeued": requeued}

    def report(self, project_id: str) -> str:
        st = self.status(project_id)
        proj = st["project"]
        if not proj:
            return "Project not found."
        done = [t for t in st["tasks"] if t["status"] == "COMPLETED"]
        failed = [t for t in st["tasks"] if t["status"] == "FAILED"]
        lines = [
            f"📊 Report — {proj['name']} ({project_id})",
            f"State: {st['lifecycle']}",
            f"Tasks: {len(done)}/{len(st['tasks'])} completed, {len(failed)} failed",
            f"Workspace: {proj['workspace']}",
        ]
        if failed:
            lines.append("Failed: " + ", ".join(f"{t['agent']}({t['error'][:60]})" for t in failed))
        if st["pending_approvals"]:
            lines.append(f"Approvals pending: {len(st['pending_approvals'])}")
        usage = self.db.usage_summary()
        if usage:
            lines.append("AI usage: " + ", ".join(
                f"{u['provider']}/{u['model']} x{u['runs']} (${u['cost'] or 0:.4f})" for u in usage[:3]))
        return "\n".join(lines)

    def shutdown(self) -> None:
        self.scheduler.stop()
        self.db.close()

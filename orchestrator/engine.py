"""Chief Orchestrator engine — ties planner, scheduler, registry, lifecycle together."""
from __future__ import annotations
import logging
import time
from pathlib import Path
from config.settings import Settings
from memory.database import Database
from memory.project_memory import ProjectMemory
from execution.file_manager import FileManager
from execution.sandbox import Sandbox
from orchestrator.lifecycle import LifecycleManager
from orchestrator.planner import TaskPlanner
from orchestrator.scheduler import Scheduler
from agents.registry import AgentRegistry
from integrations.ai_providers.router import ModelRouter
from security.approval import ApprovalManager

log = logging.getLogger("orchestrator")

class OrchestratorEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.memory = ProjectMemory(self.db)
        self.lifecycle = LifecycleManager()
        self.planner = TaskPlanner(self.db)
        self.approvals = ApprovalManager(self.db)
        self.router = ModelRouter(settings, db=self.db)
        self.scheduler = Scheduler(self.db, max_workers=settings.max_concurrent_workers)
        self.scheduler.set_worker(self._execute_task)
        self._paused = False
        self._cancelled: set[str] = set()
        self._on_progress = None  # callable(project_id, msg)

    def set_progress_callback(self, fn) -> None:
        self._on_progress = fn

    def _notify(self, project_id: str, msg: str) -> None:
        log.info("[%s] %s", project_id, msg)
        if self._on_progress:
            try:
                self._on_progress(project_id, msg)
            except Exception:
                pass

    def create_project(self, name: str, requirements: str = "", quick: bool = False) -> str:
        ws = str((self.settings.workspace_root / f"{name}_{int(time.time())}").resolve())
        Path(ws).mkdir(parents=True, exist_ok=True)
        pid = self.db.create_project(name, ws, {"requirements_preview": requirements[:500]})
        self.lifecycle.set(pid, "INPUT_RECEIVED")
        self._notify(pid, f"Project created: {name}")
        # Save requirements
        fm = FileManager(ws)
        fm.write("requirements.txt", requirements or f"Project: {name}")
        Sandbox(ws).init_git()
        self.lifecycle.set(pid, "VALIDATING")
        self.lifecycle.set(pid, "PLANNING")
        task_ids = self.planner.plan(pid, requirements, quick=quick)
        self.lifecycle.set(pid, "QUEUED")
        self._notify(pid, f"Planned {len(task_ids)} tasks. Starting workers...")
        self.scheduler.start()
        return pid

    def _execute_task(self, task: dict) -> None:
        pid = task["project_id"]
        tid = task["id"]
        agent_id = task["agent_id"]
        if pid in self._cancelled:
            self.db.set_task_status(tid, "CANCELLED")
            return
        while self._paused:
            time.sleep(0.5)
            if pid in self._cancelled:
                self.db.set_task_status(tid, "CANCELLED")
                return
        proj = self.db.get_project(pid)
        if not proj:
            self.db.set_task_status(tid, "FAILED", error="Project not found")
            return
        ws = proj["workspace"]
        fm = FileManager(ws)
        registry = AgentRegistry(workspace=ws, ai_router=self.router, db=self.db, file_manager=fm)
        eid = self.db.start_execution(pid, tid, agent_id)
        self.lifecycle.set(pid, "EXECUTING")
        self._notify(pid, f"▶ {agent_id}: {task['title']}")
        started = time.time()
        try:
            result = registry.run(agent_id, task)
            elapsed = int((time.time() - started) * 1000)
            if result.ok:
                self.db.set_task_status(tid, "COMPLETED", result=result.summary)
                self.db.finish_execution(eid, "COMPLETED")
                self.db.log(eid, "INFO", result.summary)
                self._notify(pid, f"✓ {agent_id}: {result.summary}")
                Sandbox(ws).checkpoint(f"{agent_id}: {task['title']}")
                for f in result.files:
                    try:
                        self.db.add_artifact(pid, "file", f, fm.sha256(f))
                    except Exception:
                        pass
            else:
                # Retry up to max_retries
                n = self.db.increment_retry(tid)
                maxr = task.get("max_retries", 3)
                if n < maxr:
                    self.db.set_task_status(tid, "QUEUED", error=result.error or result.summary)
                    self.db.finish_execution(eid, "FAILED")
                    self._notify(pid, f"↻ {agent_id} retry {n}/{maxr}: {result.error or result.summary}")
                else:
                    self.db.set_task_status(tid, "FAILED", error=result.error or result.summary)
                    self.db.finish_execution(eid, "FAILED", error=result.error)
                    self._notify(pid, f"✗ {agent_id} failed: {result.error or result.summary}")
        except Exception as e:
            self.db.set_task_status(tid, "FAILED", error=str(e))
            self.db.finish_execution(eid, "FAILED", error=str(e))
            self._notify(pid, f"✗ {agent_id} exception: {e}")
        # Check if project done
        tasks = self.db.list_tasks(pid)
        if tasks and all(t["status"] in ("COMPLETED", "FAILED", "SKIPPED", "CANCELLED") for t in tasks):
            failed = [t for t in tasks if t["status"] == "FAILED"]
            if failed:
                self.lifecycle.set(pid, "FAILED")
                self._notify(pid, f"Project FAILED ({len(failed)} tasks failed). Use /recover to retry.")
            else:
                self.lifecycle.set(pid, "COMPLETED")
                self.db.set_project_status(pid, "COMPLETED")
                self._notify(pid, "🎉 Project COMPLETED. Workers stopping; Telegram listener stays up.")
            self.scheduler.stop()

    def status(self, project_id: str) -> dict:
        proj = self.db.get_project(project_id)
        tasks = self.db.list_tasks(project_id) if proj else []
        return {"project": proj, "lifecycle": self.lifecycle.get(project_id),
                "tasks": [{"id": t["id"], "agent": t["agent_id"], "title": t["title"], "status": t["status"]} for t in tasks]}

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def cancel(self, project_id: str) -> None:
        self._cancelled.add(project_id)
        self.lifecycle.set(project_id, "CANCELLED")
        self.db.set_project_status(project_id, "CANCELLED")

    def recover(self, project_id: str) -> int:
        n = self.db.recover_interrupted()
        # Requeue FAILED tasks
        for t in self.db.list_tasks(project_id):
            if t["status"] == "FAILED":
                self.db.set_task_status(t["id"], "QUEUED")
        self.lifecycle.set(project_id, "RECOVERING")
        self.lifecycle.set(project_id, "QUEUED")
        self.scheduler.start()
        return n

    def shutdown(self) -> None:
        self.scheduler.stop()
        self.db.close()

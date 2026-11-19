"""Orchestrator engine — dynamic planning, handoffs, approvals, resource guard & crash recovery."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from agents.registry import AGENT_NAMES, AgentRegistry
from config.settings import Settings
from execution.document_processor import extract_directory_corpus, extract_document_text
from execution.file_manager import FileManager
from execution.packager import package_project_zip
from execution.resource_manager import ResourceManager
from execution.sandbox import Sandbox
from integrations.ai_providers.router import ModelRouter
from integrations.github.client import GitHubClient
from memory.database import Database
from memory.project_memory import ProjectMemory
from orchestrator.handoff import HandoffManager
from orchestrator.lifecycle import LifecycleManager
from orchestrator.planner import TaskPlanner
from orchestrator.scheduler import Scheduler
from security.approval import ApprovalManager, ApprovalRequiredError
from security.secret_manager import redact
from security.validation import (
    classify_upload_category,
    safe_extract_zip,
    validate_upload,
    validate_upload_content,
)

log = logging.getLogger("orchestrator")

FINAL_TASK_STATES = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}


class OrchestratorEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.memory = ProjectMemory(
            self.db,
            max_versions=getattr(settings, "memory_versions_per_key", 25),
        )
        self.lifecycle = LifecycleManager(self.db)
        self.resources = ResourceManager(
            workspace_root=settings.workspace_root,
            min_free_ram_mb_worker2=getattr(settings, "min_free_ram_mb_worker2", 600),
            min_free_disk_mb=getattr(settings, "min_free_disk_mb", 250),
        )
        self.approvals = ApprovalManager(
            self.db,
            default_ttl_seconds=getattr(settings, "approval_ttl_seconds", 3600),
            notify_callback=self._handle_new_approval,
        )
        self.router = ModelRouter(settings, db=self.db)
        self.planner = TaskPlanner(self.db, ai_router=self.router)
        self.handoffs = HandoffManager(self.db)
        self.github = GitHubClient(
            repo=getattr(settings, "github_remote_repo", "") or "arafatislambksmd378-lgtm/phone-ai-agent-team",
            token=getattr(settings, "github_token", ""),
            approval_manager=self.approvals,
        )
        self.scheduler = Scheduler(
            self.db,
            max_workers=settings.max_concurrent_workers,
            resource_manager=self.resources,
            stale_after=getattr(settings, "stale_task_seconds", 900),
        )
        self.scheduler.set_worker(self._execute_task)
        self.scheduler.set_idle_callback(self._maybe_finish_projects)
        self._paused = False
        self._cancelled: set[str] = set()
        self._on_progress = None
        self._on_approval = None
        self._on_completion = None
        self._finish_lock = threading.Lock()
        self._plan_guard = threading.Lock()
        self._project_locks: dict[str, threading.Lock] = {}

    def _lock_for_project(self, project_id: str) -> threading.Lock:
        with self._plan_guard:
            lk = self._project_locks.get(project_id)
            if lk is None:
                lk = threading.Lock()
                self._project_locks[project_id] = lk
            return lk

    # ---------- hooks ----------
    def set_progress_callback(self, fn) -> None:
        self._on_progress = fn

    def set_approval_callback(self, fn) -> None:
        """fn(approval_row_dict) — used by TelegramHandler to send inline Approve/Reject buttons."""
        self._on_approval = fn

    def set_completion_callback(self, fn) -> None:
        """fn(project_id, report_text, deliverable_zip_path) — delivers final report & ZIP."""
        self._on_completion = fn

    def _handle_new_approval(self, approval_row: dict) -> None:
        pid = approval_row.get("project_id", "")
        self._notify(
            pid,
            f"⚠️ Approval required (`{approval_row.get('id')}`) for `{approval_row.get('action')}`: "
            f"{approval_row.get('detail', '')[:160]}",
        )
        if self._on_approval is not None:
            try:
                self._on_approval(approval_row)
            except Exception:
                log.debug("approval callback failed", exc_info=True)

    def _notify(self, project_id: str, msg: str) -> None:
        safe = redact(msg)
        log.info("[%s] %s", project_id, safe)
        if self._on_progress:
            try:
                self._on_progress(project_id, safe)
            except Exception:
                log.debug("progress callback failed", exc_info=True)

    # ---------- startup crash / Termux restart recovery ----------
    def startup_recovery(self) -> dict:
        """Detect tasks interrupted by a Termux kill/crash, requeue safely, and resume workers."""
        dangling = self.db.detect_interrupted()
        if not dangling:
            return {"interrupted": 0, "projects": []}
        affected = sorted({t["project_id"] for t in dangling})
        requeued = self.db.recover_interrupted()
        for pid in affected:
            state = self.lifecycle.get(pid)
            if state in ("EXECUTING", "FAILED", "PAUSED"):
                self.lifecycle.set(pid, "RECOVERING")
                self.lifecycle.set(pid, "QUEUED")
            self._notify(pid, f"♻️ Startup recovery requeued {requeued} interrupted task(s).")
        if requeued > 0:
            self.scheduler.start()
        return {"interrupted": requeued, "projects": affected}

    # ---------- project intake & dynamic planning ----------
    def create_project(self, name: str, requirements: str = "", quick: bool = False,
                       *, auto_start: bool = True, dynamic: bool = True,
                       source: str = "system", description: str = "") -> str:
        safe_name = "".join(c for c in (name or "project") if c.isalnum() or c in "-_")[:40] or "project"
        ws = str((self.settings.workspace_root / f"{safe_name}_{int(time.time() * 1000)}").resolve())
        Path(ws).mkdir(parents=True, exist_ok=True)
        pid = self.db.create_project(safe_name, ws, {
            "requirements_preview": (requirements or "")[:500],
            "description": (description or "")[:1000],
            "created_via": source,
        })
        if description:
            self.db.execute("UPDATE projects SET description=? WHERE id=?", ((description or "")[:1000], pid))
        self.db.record_sync(pid, source, "project_created", safe_name)
        self.lifecycle.set(pid, "INPUT_RECEIVED")
        self._notify(pid, f"📁 Project created ({source}): {safe_name}")
        FileManager(ws).write("requirements.txt", requirements or f"Project: {name}")
        sb = Sandbox(ws)
        sb.init_git()
        sha = sb.checkpoint_commit("initial blueprint")
        if sha:
            self.db.record_checkpoint(pid, "initial blueprint", sha)

        if auto_start:
            self.start_project(pid, requirements=requirements, quick=quick, dynamic=dynamic, source=source)
        return pid

    def start_project(self, project_id: str, requirements: str | None = None,
                      quick: bool = False, *, dynamic: bool = True,
                      source: str = "system") -> list[str]:
        """Validate blueprint, dynamically plan the Task DAG, and launch on-demand worker(s)."""
        proj = self.db.get_project(project_id)
        if not proj:
            raise ValueError(f"Unknown project: {project_id}")
        with self._lock_for_project(project_id):
            fm = FileManager(proj["workspace"])
            if requirements is not None and requirements.strip():
                fm.write("requirements.txt", requirements[:400_000])
            req_text = requirements if requirements is not None else (
                fm.read("requirements.txt") if fm.exists("requirements.txt") else ""
            )
            self.lifecycle.set(project_id, "VALIDATING")
            self.lifecycle.set(project_id, "PLANNING")
            task_ids = self.planner.plan(project_id, req_text, quick=quick, dynamic=dynamic)
            analysis = self.db.get_memory(project_id).get("blueprint_analysis") or {}
            stype = analysis.get("software_type", "web_app")
            excluded = list((analysis.get("excluded_agents") or {}).keys())
            self.lifecycle.set(project_id, "QUEUED")
            self.db.record_sync(
                project_id, source, "project_planned",
                f"{stype}: {len(task_ids)} tasks planned",
            )
            excl_note = f" (excluded {len(excluded)} irrelevant agents)" if excluded else ""
            self._notify(
                project_id,
                f"Planned {len(task_ids)} tasks for `{stype}` via {source}{excl_note}. Starting worker pool…",
            )
        self.scheduler.start()
        return task_ids

    def replan_project(self, project_id: str, additional_requirements: str = "",
                       execution_feedback: str = "", *, source: str = "system") -> dict:
        """Update requirements and dynamically replan the project's Task DAG (concurrency-safe)."""
        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "error": "Project not found"}
        if additional_requirements:
            self._append_requirements(project_id, additional_requirements)
        fm = FileManager(proj["workspace"])
        full_req = fm.read("requirements.txt") if fm.exists("requirements.txt") else additional_requirements

        if not self.db.list_tasks(project_id):
            tids = self.start_project(project_id, requirements=full_req, source=source)
            analysis = self.db.get_memory(project_id).get("blueprint_analysis") or {}
            return {
                "ok": True,
                "started": True,
                "task_count": len(tids),
                "task_ids": tids,
                "software_type": analysis.get("software_type", "web_app"),
                "selected_agents": analysis.get("selected_agents", []),
                "excluded_agents": analysis.get("excluded_agents", {}),
            }

        with self._lock_for_project(project_id):
            info = self.planner.replan(project_id, full_req, execution_feedback=execution_feedback)
            state = self.lifecycle.get(project_id)
            if state in ("FAILED", "PAUSED"):
                self.lifecycle.set(project_id, "RECOVERING")
                self.lifecycle.set(project_id, "QUEUED")
            elif state == "COMPLETED" and (info["added_agents"] or info["requeued_agents"]):
                self.lifecycle.set(project_id, "QUEUED")
            self.db.record_sync(
                project_id, source, "project_replanned",
                f"{info['software_type']}: +{len(info['added_agents'])} added, {len(info['requeued_agents'])} requeued",
            )
        if info["added_agents"] or info["requeued_agents"]:
            self.scheduler.start()
            self._notify(
                project_id,
                f"🔄 Replanned `{info['software_type']}` ({source}): "
                f"+{len(info['added_agents'])} added, {len(info['requeued_agents'])} requeued.",
            )
        return {"ok": True, **info}

    def create_single_task(self, project_id: str, agent_id: str, title: str,
                           instruction: str = "", *, priority: int = 100,
                           depends_on: list[str] | None = None,
                           auto_run: bool = True, source: str = "dashboard",
                           idempotency_key: str = "") -> dict:
        """Create (idempotently per project+agent+title) a task and optionally run it."""
        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "status": 404, "error": "Project not found"}
        if agent_id not in AGENT_NAMES:
            return {"ok": False, "status": 400, "error": f"Unknown agent_id: {agent_id!r}"}
        clean_title = (title or "").strip()
        if not clean_title:
            return {"ok": False, "status": 400, "error": "Task title is required"}
        with self._lock_for_project(project_id):
            existing = None
            if idempotency_key:
                existing = self.db.query_one(
                    "SELECT id, status FROM tasks WHERE project_id=? AND idempotency_key=?",
                    (project_id, idempotency_key[:160]),
                )
            if not existing:
                existing = self.db.query_one(
                    "SELECT id, status FROM tasks WHERE project_id=? AND agent_id=? AND title=?",
                    (project_id, agent_id, clean_title[:200]),
                )
            if existing:
                return {
                    "ok": True,
                    "status": 200,
                    "id": existing["id"],
                    "task_status": existing["status"],
                    "deduplicated": True,
                }
            if instruction and instruction.strip():
                self._append_requirements(project_id, f"\n--- Task ({agent_id}): {clean_title} ---\n{instruction.strip()}")
            fm = FileManager(proj["workspace"])
            req_text = fm.read("requirements.txt") if fm.exists("requirements.txt") else instruction
            payload = {
                "instruction": (instruction or "")[:8000],
                "requirements": (req_text or instruction or "")[:8000],
                "source": source,
            }
            if agent_id == "workspace_executor":
                payload.setdefault("command", "ls -la")
            tid = self.db.create_task(
                project_id, agent_id, clean_title[:200], payload=payload,
                priority=max(1, min(1000, int(priority or 100))),
                depends_on=depends_on, idempotency_key=idempotency_key,
            )
            self.db.record_sync(project_id, source, "task_created", f"{tid} ({agent_id}: {clean_title[:80]})")
            cur_lc = self.lifecycle.get(project_id)
            if auto_run and cur_lc in ("IDLE", "INPUT_RECEIVED", "COMPLETED", "FAILED"):
                self.lifecycle.set(project_id, "QUEUED")
        self._notify(project_id, f"📝 Task created ({source}): `{agent_id}` — {clean_title[:100]}")
        if auto_run:
            self.scheduler.start()
        return {"ok": True, "status": 201, "id": tid, "task_status": "QUEUED", "deduplicated": False}

    def ingest_upload_detailed(self, project_id: str, local_path: str, original_name: str,
                               *, auto_plan_if_waiting: bool = False,
                               source: str = "dashboard", category: str | None = None,
                               caption: str = "") -> dict:
        """Validate, extract/stage an uploaded blueprint, ZIP, source file, document, or image."""
        import hashlib

        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "status": 404, "error": "Project not found"}
        safe_filename = Path(original_name or "").name
        size = Path(local_path).stat().st_size
        ok, msg = validate_upload(safe_filename, size, self.settings.max_upload_mb)
        if not ok:
            return {"ok": False, "status": 400, "error": msg}
        ok, msg = validate_upload_content(safe_filename, local_path)
        if not ok:
            return {"ok": False, "status": 400, "error": msg}

        raw_bytes = Path(local_path).read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        detected_cat = classify_upload_category(safe_filename)
        allowed_cats = {"blueprint", "zip", "source", "image", "document"}
        resolved_cat = detected_cat
        if category and category.strip().lower() in allowed_cats:
            req_cat = category.strip().lower()
            if req_cat == "blueprint" and detected_cat in ("document", "source"):
                resolved_cat = "blueprint"

        ws = Path(proj["workspace"])
        ws.mkdir(parents=True, exist_ok=True)
        auto_planned = False
        clean_caption = redact((caption or "").strip())[:1000]

        if detected_cat == "zip":
            ok_z, msg_z, files = safe_extract_zip(
                local_path,
                str(ws / "extracted"),
                max_files=self.settings.max_zip_files,
                max_total_mb=self.settings.max_zip_mb,
                max_archive_mb=self.settings.max_upload_mb,
            )
            if not ok_z:
                return {"ok": False, "status": 400, "error": msg_z}
            extracted_text = extract_directory_corpus(ws / "extracted", max_files=25, max_chars=60_000)
            note = f"Note: {clean_caption}\n" if clean_caption else ""
            header = f"\n--- Blueprint archive ({source}): {safe_filename} ({len(files)} files) ---\n{note}"
            self._append_requirements(project_id, header + extracted_text)
            summary = f"Extracted {len(files)} files"
            uid = self.db.record_uploaded_artifact(
                project_id, safe_filename, sha256=digest, source=source,
                category="zip", size_bytes=size, stored_path="extracted/", summary=summary,
            )
            self.db.record_sync(project_id, source, "artifact_uploaded", f"zip:{safe_filename} ({len(files)} files)")
            if auto_plan_if_waiting and self.lifecycle.get(project_id) in ("IDLE", "INPUT_RECEIVED") and not self.db.list_tasks(project_id):
                self.start_project(project_id, source=source)
                auto_planned = True
            self._notify(project_id, f"📦 Uploaded ZIP ({source}): {safe_filename} — {summary}")
            return {
                "ok": True,
                "status": 201,
                "upload_id": uid,
                "filename": safe_filename,
                "category": "zip",
                "size_bytes": size,
                "sha256": digest,
                "extracted_files": files[:100],
                "extracted_count": len(files),
                "auto_planned": auto_planned,
                "message": msg_z,
            }

        subdir = {
            "image": "images",
            "source": "source",
            "blueprint": "blueprints",
            "document": "docs",
        }.get(resolved_cat, "docs")
        dest = ws / "uploads" / subdir / safe_filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
        try:
            dest.chmod(0o600)
        except OSError:
            pass

        doc = extract_document_text(dest, safe_filename, max_chars=80_000)
        if not doc.get("ok"):
            return {"ok": False, "status": 400, "error": doc.get("warning") or "Could not process uploaded file"}
        extracted_text = doc.get("text") or ""
        caption_line = f"\nUser note: {clean_caption}" if clean_caption else ""
        self._append_requirements(
            project_id,
            f"\n--- Uploaded {resolved_cat} ({source}): {safe_filename} ({doc['kind']}) ---{caption_line}\n{extracted_text}",
        )
        summary = (
            extracted_text[:200]
            if resolved_cat == "image"
            else f"Received {safe_filename} ({size} bytes, {doc['chars']} chars extracted)"
        )
        rel_stored = str(dest.relative_to(ws))
        uid = self.db.record_uploaded_artifact(
            project_id, safe_filename, sha256=digest, source=source,
            category=resolved_cat, size_bytes=size, stored_path=rel_stored, summary=summary,
        )
        self.db.record_sync(project_id, source, "artifact_uploaded", f"{resolved_cat}:{safe_filename}")
        if auto_plan_if_waiting and self.lifecycle.get(project_id) in ("IDLE", "INPUT_RECEIVED") and not self.db.list_tasks(project_id):
            self.start_project(project_id, source=source)
            auto_planned = True
        self._notify(project_id, f"📎 Uploaded {resolved_cat} ({source}): {safe_filename}")
        return {
            "ok": True,
            "status": 201,
            "upload_id": uid,
            "filename": safe_filename,
            "category": resolved_cat,
            "kind": doc["kind"],
            "size_bytes": size,
            "sha256": digest,
            "chars_extracted": doc["chars"],
            "stored_path": rel_stored,
            "auto_planned": auto_planned,
            "message": summary,
        }

    def ingest_upload(self, project_id: str, local_path: str, original_name: str,
                      *, auto_plan_if_waiting: bool = False,
                      source: str = "system") -> tuple[bool, str]:
        """Backward-compatible wrapper returning (ok, message)."""
        res = self.ingest_upload_detailed(
            project_id, local_path, original_name,
            auto_plan_if_waiting=auto_plan_if_waiting, source=source,
        )
        if res.get("ok"):
            return True, str(res.get("message") or "OK")
        return False, str(res.get("error") or "Upload failed")

    def _append_requirements(self, project_id: str, text: str) -> None:
        proj = self.db.get_project(project_id)
        if not proj:
            return
        fm = FileManager(proj["workspace"])
        existing = fm.read("requirements.txt") if fm.exists("requirements.txt") else ""
        fm.write("requirements.txt", (existing + "\n" + (text or ""))[:400_000])

    # ---------- task execution ----------
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
        if self.db.is_task_cancel_requested(tid):
            self.db.set_task_status(tid, "CANCELLED", error="Cancelled by owner before execution")
            self._notify(pid, f"⛔ {agent_id}: task {tid} cancelled before execution.")
            self._maybe_finish_project(pid)
            return

        # 1. Android Resource Manager admission check
        res_ok, res_msg, _ = self.resources.check_resources_for_task()
        if not res_ok:
            self.db.set_task_status(tid, "FAILED", error=res_msg)
            self._notify(pid, f"⚠️ Resource guard paused `{agent_id}`: {res_msg}")
            self._maybe_finish_project(pid)
            return

        # 2. Paid AI usage threshold approval gate
        cost_cap = float(getattr(self.settings, "paid_cost_approval_usd", 0.0) or 0.0)
        if cost_cap > 0 and self.router.mode() == "live":
            spent = self.db.total_cost_usd(pid)
            if spent >= cost_cap and not self._has_approved_action(pid, "paid_api_usage"):
                aid = self.approvals.request(
                    pid,
                    "paid_api_usage",
                    f"AI spend (${spent:.4f}) reached threshold (${cost_cap:.2f})",
                    task_id=tid,
                )
                self.db.set_task_status(tid, "QUEUED", error=f"Waiting for paid_api_usage approval {aid}")
                self.lifecycle.set(pid, "WAITING_FOR_APPROVAL")
                return

        # 3. Validate upstream dependencies & build rich Agent-to-Agent handoff payload
        deps_ok, unmet = self.handoffs.validate_dependencies(tid)
        if not deps_ok:
            self.db.set_task_status(tid, "QUEUED", error=f"Waiting on dependencies: {unmet}")
            return

        enriched_payload = self.handoffs.prepare_task_input(pid, task)
        ws = proj["workspace"]
        fm = FileManager(ws, approval_manager=self.approvals, project_id=pid)
        registry = AgentRegistry(
            workspace=ws,
            ai_router=self.router,
            db=self.db,
            file_manager=fm,
            approval_manager=self.approvals,
            process_manager=self.resources.processes,
            project_id=pid,
        )

        eid = self.db.start_execution(pid, tid, agent_id)
        self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "RUNNING")
        if self.lifecycle.get(pid) in ("QUEUED", "RECOVERING", "WAITING_FOR_APPROVAL"):
            self.lifecycle.set(pid, "EXECUTING")
        if agent_id in ("qa_tester", "debugger") and self.lifecycle.get(pid) == "EXECUTING":
            self.lifecycle.set(pid, "TESTING")
        elif agent_id in ("code_reviewer", "release_manager") and self.lifecycle.get(pid) in ("EXECUTING", "TESTING"):
            self.lifecycle.set(pid, "REVIEWING")

        self._notify(pid, f"▶ {agent_id}: {task['title']}")
        task_for_agent = dict(task)
        task_for_agent["payload"] = enriched_payload
        task_for_agent["execution_id"] = eid

        started = time.time()
        err = ""
        result = None
        try:
            result = registry.run(agent_id, task_for_agent)
        except ApprovalRequiredError as ae:
            err = str(ae)
            self.db.finish_execution(eid, "WAITING_FOR_APPROVAL", error=err)
            self.db.set_task_status(tid, "QUEUED", error=err)
            self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "IDLE")
            if self.lifecycle.get(pid) in ("TESTING", "REVIEWING"):
                self.lifecycle.set(pid, "EXECUTING")
            self.lifecycle.set(pid, "WAITING_FOR_APPROVAL")
            self._notify(pid, f"⏸ `{agent_id}` paused awaiting approval `{ae.approval_id}`.")
            return
        except Exception as e:
            log.exception("agent %s crashed", agent_id)
            err = f"{type(e).__name__}: {e}"
        finally:
            self.resources.post_task_housekeeping(ws)

        # A persisted cancel request wins over a completed agent run: no handoff,
        # no checkpoint and no downstream task may be published after cancellation.
        if self.db.is_task_cancel_requested(tid):
            self.db.finish_execution(eid, "CANCELLED", error="Cancelled by owner during execution")
            self.db.set_task_status(tid, "CANCELLED", error="Cancelled by owner during execution")
            self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "IDLE")
            self._notify(pid, f"⛔ {agent_id}: task {tid} cancelled; result discarded.")
            self._maybe_finish_project(pid)
            return

        elapsed_s = time.time() - started
        elapsed_ms = int(elapsed_s * 1000)

        # Enforce configured task timeout ceiling honestly
        if elapsed_s > max(1, int(self.settings.task_timeout_seconds)):
            result = None
            err = f"Task exceeded timeout ({int(elapsed_s)}s > {self.settings.task_timeout_seconds}s)"

        # Handle structured approval pause returned via AgentResult.data
        if result is not None and not result.ok and (result.data or {}).get("needs_approval"):
            aid = (result.data or {}).get("approval_id", "")
            self.db.finish_execution(eid, "WAITING_FOR_APPROVAL", error=result.error or result.summary)
            self.db.set_task_status(tid, "QUEUED", error=f"Waiting for approval {aid}")
            self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "IDLE")
            if self.lifecycle.get(pid) in ("TESTING", "REVIEWING"):
                self.lifecycle.set(pid, "EXECUTING")
            self.lifecycle.set(pid, "WAITING_FOR_APPROVAL")
            return

        if result is not None and result.ok:
            handoff = self.handoffs.verify_and_record(pid, task, result, fm)
            self.db.set_task_status(
                tid,
                "COMPLETED",
                result=result.summary,
                output_data=handoff.to_dict(),
            )
            self.db.finish_execution(eid, "COMPLETED")
            self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "IDLE")
            self.db.log(eid, "INFO", f"{agent_id} ok in {elapsed_ms}ms: {result.summary}")
            try:
                self.memory.remember_entry(
                    pid, "task_result", tid, result.summary or "(no summary)",
                    source=agent_id, task_id=tid)
            except Exception:
                log.debug("could not persist task_result memory", exc_info=True)
            self._notify(pid, f"✓ {agent_id}: {result.summary}")
            sha = Sandbox(ws).checkpoint_commit(f"{agent_id}: {task['title']}")
            if sha:
                self.db.record_checkpoint(pid, f"{agent_id}: {task['title']}", sha)
            for vf in handoff.verified_files:
                try:
                    self.db.add_artifact(pid, "file", vf["path"], vf["sha256"])
                except Exception:
                    pass
        else:
            reason = err or (result.error if result else "") or (result.summary if result else "unknown error")
            n = self.db.increment_retry(tid)
            max_retries = int(task.get("max_retries") or 3)
            self.db.finish_execution(eid, "FAILED", error=reason)
            self.db.update_agent_status(agent_id, AGENT_NAMES.get(agent_id, agent_id), "IDLE")
            self.db.log(eid, "ERROR", f"{agent_id} failed: {reason}")
            try:
                self.memory.remember_entry(
                    pid, "task_failure", tid, reason,
                    source=agent_id, task_id=tid)
            except Exception:
                log.debug("could not persist task_failure memory", exc_info=True)
            if n < max_retries:
                # Deterministic exponential backoff: the queue will not claim the
                # task again before not_before, avoiding API/GitHub retry storms.
                base = max(1, int(getattr(self.settings, "retry_backoff_base_seconds", 2)))
                ceiling = max(base, int(getattr(self.settings, "retry_backoff_max_seconds", 300)))
                delay = min(ceiling, base ** max(0, n - 1))
                scheduled = self.db.requeue_with_backoff(tid, delay, reason)
                if not scheduled:  # task was finalised concurrently (e.g. owner cancelled)
                    self._maybe_finish_project(pid)
                    return
                self._notify(
                    pid,
                    f"↻ {agent_id} retry {n}/{max_retries} in {delay}s: {reason[:200]}",
                )
            else:
                self.db.set_task_status(tid, "FAILED", error=reason)
                skipped = self.handoffs.propagate_failure(pid)
                extra = f" ({skipped} dependent task(s) skipped)" if skipped else ""
                self._notify(pid, f"✗ {agent_id} failed: {reason[:260]}{extra}")

        self._maybe_finish_project(pid)

    def _has_approved_action(self, project_id: str, action: str) -> bool:
        rows = self.db.query(
            "SELECT id FROM approvals WHERE project_id=? AND action=? AND status='APPROVED'",
            (project_id, action),
        )
        return any(self.approvals.is_approved(r["id"]) for r in rows)

    def on_approval_decided(self, approval_id: str, approved: bool, *, source: str = "system") -> None:
        """Resume or fail waiting tasks and broadcast synchronized approval decision."""
        row = self.db.get_approval(approval_id)
        if not row:
            return
        pid = row["project_id"]
        tid = row.get("task_id")
        actor = source or row.get("decided_by") or "system"
        self.db.record_sync(
            pid, actor, "approval_approved" if approved else "approval_rejected",
            f"{approval_id} ({row.get('action', '')})",
        )
        if actor in ("dashboard", "android"):
            verdict = "Approved" if approved else "Rejected"
            self._notify(pid, f"🔐 {verdict} via {actor}: `{approval_id}` ({row.get('action', '')})")
        if approved:
            if tid:
                t = self.db.get_task(tid)
                if t:
                    payload = self.handoffs.prepare_task_input(pid, t)
                    payload["approval_id"] = approval_id
                    payload["approved"] = True
                    self.db.update_task_payload(tid, payload)
            if self.lifecycle.get(pid) == "WAITING_FOR_APPROVAL":
                self.lifecycle.set(pid, "QUEUED")
            self.scheduler.start()
        else:
            if tid:
                self.db.set_task_status(tid, "FAILED", error=f"Approval {approval_id} rejected by user")
                self.handoffs.propagate_failure(pid)
            if self.lifecycle.get(pid) == "WAITING_FOR_APPROVAL":
                self.lifecycle.set(pid, "EXECUTING")
            self._maybe_finish_project(pid)

    # ---------- project completion ----------
    def _maybe_finish_projects(self) -> None:
        """When the pool is idle, close out any project whose tasks are all final."""
        if self.scheduler.active_count() > 0:
            return
        for proj in self.db.query(
            "SELECT id FROM projects WHERE lifecycle IN ('QUEUED','EXECUTING','RECOVERING','TESTING','REVIEWING')"
        ):
            self._maybe_finish_project(proj["id"])

    def _maybe_finish_project(self, pid: str) -> bool:
        if pid in self._cancelled:
            return False
        tasks = self.db.list_tasks(pid)
        if not tasks:
            return False
        if not all(t["status"] in FINAL_TASK_STATES for t in tasks):
            # Ensure permanent upstream failures propagate even if set externally
            if any(t["status"] == "FAILED" for t in tasks):
                self.handoffs.propagate_failure(pid)
                tasks = self.db.list_tasks(pid)
            if not all(t["status"] in FINAL_TASK_STATES for t in tasks):
                return False

        delivered_zip = ""
        completed_now = False

        with self._finish_lock:
            state = self.lifecycle.get(pid)
            if state in ("COMPLETED", "CANCELLED"):
                return False
            failed = [t for t in tasks if t["status"] == "FAILED"]
            cancelled = [t for t in tasks if t["status"] == "CANCELLED"]
            pending_approvals = self.db.pending_approvals(pid)
            if pending_approvals:
                if state in ("QUEUED", "TESTING", "REVIEWING"):
                    self.lifecycle.set(pid, "EXECUTING")
                self.lifecycle.set(pid, "WAITING_FOR_APPROVAL")
                self._notify(pid, f"⏸ {len(pending_approvals)} approval(s) pending.")
                return False
            if failed:
                if state in ("QUEUED", "TESTING", "REVIEWING"):
                    self.lifecycle.set(pid, "EXECUTING")
                if self.lifecycle.set(pid, "FAILED"):
                    self._notify(pid, f"Project FAILED ({len(failed)} task(s)). Use /recover to retry.")
            elif cancelled:
                # Owner cancellation must never be reported as a successful delivery.
                if state in ("QUEUED", "TESTING", "REVIEWING"):
                    self.lifecycle.set(pid, "EXECUTING")
                if self.lifecycle.set(pid, "CANCELLED"):
                    self._notify(
                        pid,
                        f"Project CANCELLED ({len(cancelled)} task(s) cancelled by the owner).",
                    )
            else:
                for intermediate in ("EXECUTING", "TESTING", "REVIEWING"):
                    if self.lifecycle.get(pid) == intermediate:
                        continue
                    self.lifecycle.set(pid, intermediate)
                if self.lifecycle.set(pid, "COMPLETED"):
                    completed_now = True
                    proj = self.db.get_project(pid)
                    if proj:
                        pkg = package_project_zip(proj["workspace"])
                        if pkg.get("ok"):
                            delivered_zip = pkg["zip_path"]
                            self.db.add_artifact(pid, "deliverable_zip", delivered_zip, pkg["sha256"])
                    self._notify(pid, "🎉 Project COMPLETED. Workers stopped; Telegram listener stays up.")

            if not self.db.query(
                "SELECT id FROM projects WHERE lifecycle IN ('QUEUED','EXECUTING','RECOVERING','TESTING','REVIEWING')"
            ):
                self.scheduler.stop()

        if completed_now and self._on_completion is not None:
            try:
                self._on_completion(pid, self.report(pid), delivered_zip)
            except Exception:
                log.debug("completion callback failed", exc_info=True)
        return True

    # ---------- durable per-task control ----------
    def control_task(self, project_id: str, task_id: str, action: str,
                     *, actor: str = "system") -> dict:
        """Cancel/retry/resume one task through the shared, audited backend."""
        task = self.db.get_task(task_id)
        if not task or task["project_id"] != project_id:
            return {"ok": False, "status": 404, "error": "Task not found in this project"}
        action = str(action).strip().lower()
        with self._lock_for_project(project_id):
            if action == "cancel":
                result = self.db.request_task_cancel(task_id, actor=actor)
            elif action in ("retry", "resume"):
                result = self.db.requeue_task(task_id, action, actor=actor)
            else:
                return {"ok": False, "status": 400,
                        "error": "action must be one of cancel, retry, resume"}
        if not result.get("ok"):
            return {"ok": False, "status": 409, **result}
        self.db.record_sync(
            project_id, actor, f"task_{action}",
            f"{task_id} ({task['agent_id']}): {result.get('status', '')}",
        )
        if result.get("status") == "QUEUED":
            self.lifecycle.set(project_id, "QUEUED")
            self.scheduler.start()
        elif action == "cancel" and result.get("effective") == "cancelled":
            state = self.lifecycle.get(project_id)
            if state in ("QUEUED", "EXECUTING", "TESTING", "REVIEWING"):
                self.lifecycle.set(project_id, "EXECUTING")
            self._maybe_finish_project(project_id)
        self._notify(
            project_id,
            f"🎛 {actor} {action}d task {task_id} ({task['agent_id']}): {result.get('status')}",
        )
        return {"ok": True, "status": 200, "task_id": task_id, **result}

    # ---------- control & reporting ----------
    def status(self, project_id: str) -> dict:
        proj = self.db.get_project(project_id)
        tasks = self.db.list_tasks(project_id) if proj else []
        mem = self.db.get_memory(project_id) if proj else {}
        return {
            "project": proj,
            "lifecycle": self.lifecycle.get(project_id) if proj else "UNKNOWN",
            "blueprint_analysis": mem.get("blueprint_analysis") or {},
            "tasks": [
                {
                    "id": t["id"],
                    "agent": t["agent_id"],
                    "title": t["title"],
                    "status": t["status"],
                    "summary": t.get("result") or "",
                    "error": t["error"],
                    "retries": t["retries"],
                    "cancel_requested": bool(t.get("cancel_requested", 0)),
                    "not_before": t.get("not_before", 0),
                }
                for t in tasks
            ],
            "pending_approvals": self.db.pending_approvals(project_id) if proj else [],
        }

    def agent_statuses(self) -> list[dict]:
        db_rows = {r["id"]: r for r in self.db.query("SELECT * FROM agents")}
        out = []
        for aid, name in AGENT_NAMES.items():
            row = db_rows.get(aid) or {}
            out.append({
                "id": aid,
                "name": name,
                "status": row.get("status", "IDLE"),
                "last_run_at": row.get("last_run_at"),
            })
        return out

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        if self.db.query("SELECT id FROM tasks WHERE status='QUEUED' LIMIT 1"):
            self.scheduler.start()

    def cancel(self, project_id: str) -> None:
        self._cancelled.add(project_id)
        # Persist the intent as well: a restarted backend must not reclaim work
        # that the owner cancelled in a previous process.
        self.db.execute(
            "UPDATE tasks SET cancel_requested=1 WHERE project_id=? "
            "AND status IN ('QUEUED','EXECUTING')", (project_id,))
        for t in self.db.list_tasks(project_id):
            if t["status"] in ("QUEUED", "EXECUTING"):
                self.db.set_task_status(t["id"], "CANCELLED", error="Project cancelled")
        self.lifecycle.set(project_id, "CANCELLED")
        self.db.set_project_status(project_id, "CANCELLED", lifecycle="CANCELLED")

    def recover(self, project_id: str) -> dict:
        proj = self.db.get_project(project_id)
        if not proj:
            return {"ok": False, "error": "Project not found"}
        self._cancelled.discard(project_id)
        interrupted = self.db.recover_interrupted()
        requeued = self.db.requeue_failed(project_id)
        cur_state = self.lifecycle.get(project_id)
        if cur_state in ("FAILED", "PAUSED", "EXECUTING"):
            self.lifecycle.set(project_id, "RECOVERING")
            self.lifecycle.set(project_id, "QUEUED")
        elif cur_state == "RECOVERING":
            self.lifecycle.set(project_id, "QUEUED")
        self.scheduler.start()
        self._notify(project_id, f"♻️ Recovered: {interrupted} interrupted, {requeued} failed requeued.")
        return {"ok": True, "interrupted": interrupted, "requeued": requeued}

    def deliverable_zip_path(self, project_id: str) -> str:
        proj = self.db.get_project(project_id)
        if not proj:
            return ""
        arts = self.db.query(
            "SELECT path FROM artifacts WHERE project_id=? AND kind='deliverable_zip' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
        if arts and Path(arts[0]["path"]).is_file():
            return arts[0]["path"]
        pkg = package_project_zip(proj["workspace"])
        if pkg.get("ok"):
            self.db.add_artifact(project_id, "deliverable_zip", pkg["zip_path"], pkg["sha256"])
            return pkg["zip_path"]
        return ""

    def report(self, project_id: str) -> str:
        st = self.status(project_id)
        proj = st["project"]
        if not proj:
            return "Project not found."
        done = [t for t in st["tasks"] if t["status"] == "COMPLETED"]
        failed = [t for t in st["tasks"] if t["status"] == "FAILED"]
        skipped = [t for t in st["tasks"] if t["status"] == "SKIPPED"]
        analysis = st.get("blueprint_analysis") or {}
        stype = analysis.get("software_type", "web_app")
        excluded = list((analysis.get("excluded_agents") or {}).keys())
        lines = [
            f"📊 Report — {proj['name']} ({project_id})",
            f"State: {st['lifecycle']} | Category: {stype}",
            f"Tasks: {len(done)}/{len(st['tasks'])} completed, {len(failed)} failed, {len(skipped)} skipped",
            f"Workspace: {proj['workspace']}",
        ]
        if excluded:
            lines.append("Excluded agents: " + ", ".join(excluded))
        if failed:
            lines.append("Failed: " + ", ".join(f"{t['agent']}({(t['error'] or '')[:60]})" for t in failed))
        if st["pending_approvals"]:
            lines.append(f"Approvals pending: {len(st['pending_approvals'])}")
        usage = self.db.usage_summary()
        if usage:
            lines.append("AI usage: " + ", ".join(
                f"{u['provider']}/{u['model']} x{u['runs']} (${u['cost'] or 0:.4f})" for u in usage[:3]
            ))
        return "\n".join(lines)

    def shutdown(self) -> None:
        self.scheduler.stop()
        self.resources.shutdown()
        self.db.close()

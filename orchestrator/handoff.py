"""Agent-to-Agent Handoff Manager — contracts, dependency verification, context budgeting."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from execution.file_manager import FileManager
from memory.database import Database


@dataclass
class TaskHandoff:
    from_task_id: str
    from_agent_id: str
    summary: str
    files: list[str] = field(default_factory=list)
    verified_files: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    mode: str = "deterministic"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_task_id": self.from_task_id,
            "from_agent_id": self.from_agent_id,
            "summary": self.summary,
            "files": self.files,
            "verified_files": self.verified_files,
            "data": self.data,
            "mode": self.mode,
            "created_at": self.created_at,
        }


class HandoffManager:
    """Persists verified upstream agent outputs and constructs bounded context for downstream agents."""

    def __init__(self, db: Database, max_handoff_chars: int = 12_000):
        self.db = db
        self.max_handoff_chars = max(2000, int(max_handoff_chars))

    def verify_and_record(self, project_id: str, task: dict, result,
                          fm: FileManager) -> TaskHandoff:
        """Verify declared artifacts exist on disk, persist handoff & update shared project memory."""
        tid = task["id"]
        agent_id = task["agent_id"]
        verified_files: list[dict[str, str]] = []
        valid_rels: list[str] = []

        for rel in (result.files or []):
            if fm.exists(rel):
                valid_rels.append(rel)
                try:
                    verified_files.append({"path": rel, "sha256": fm.sha256(rel)})
                except Exception:
                    verified_files.append({"path": rel, "sha256": ""})

        bounded_data = _bound_dict(result.data or {}, max_chars=4000)
        handoff = TaskHandoff(
            from_task_id=tid,
            from_agent_id=agent_id,
            summary=(result.summary or "")[:1000],
            files=valid_rels,
            verified_files=verified_files,
            data=bounded_data,
            mode=getattr(result, "mode", "deterministic"),
        )

        # 1. Persist in task_handoffs table
        self.db.record_handoff(
            project_id=project_id,
            from_task_id=tid,
            from_agent_id=agent_id,
            summary=handoff.summary,
            artifacts=valid_rels,
            data=handoff.to_dict(),
        )

        # 2. Update shared ProjectMemory with structured agent output
        mem = self.db.get_memory(project_id)
        agent_outputs = mem.get("agent_outputs") or {}
        agent_outputs[agent_id] = {
            "task_id": tid,
            "summary": handoff.summary,
            "files": valid_rels,
            "mode": handoff.mode,
            "data": _bound_dict(bounded_data, max_chars=2000),
            "ts": handoff.created_at,
        }
        mem["agent_outputs"] = agent_outputs
        all_artifacts = set(mem.get("artifacts") or [])
        all_artifacts.update(valid_rels)
        mem["artifacts"] = sorted(all_artifacts)
        self.db.set_memory(project_id, mem)

        return handoff

    def validate_dependencies(self, task_id: str) -> tuple[bool, list[str]]:
        """Ensure all upstream dependencies reached COMPLETED or SKIPPED."""
        deps = self.db.get_task_dependencies(task_id)
        unmet = [f"{d['agent_id']}({d['status']})" for d in deps
                 if d["status"] not in ("COMPLETED", "SKIPPED")]
        return len(unmet) == 0, unmet

    def prepare_task_input(self, project_id: str, task: dict) -> dict[str, Any]:
        """Enrich a claimed task's payload with actual upstream outputs, memory, and retry history."""
        try:
            payload = json.loads(task.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}

        deps = self.db.get_task_dependencies(task["id"])
        upstream: dict[str, Any] = {}
        used_chars = 0
        per_dep_budget = max(800, self.max_handoff_chars // max(1, len(deps) + 1))

        for dep in deps:
            try:
                out_data = json.loads(dep.get("output_data") or "{}")
            except (json.JSONDecodeError, TypeError):
                out_data = {}
            entry = {
                "task_id": dep["id"],
                "status": dep["status"],
                "summary": (dep.get("result") or "")[:500],
                "output": _bound_dict(out_data, max_chars=per_dep_budget),
            }
            serialized = json.dumps(entry)
            if used_chars + len(serialized) <= self.max_handoff_chars:
                upstream[dep["agent_id"]] = entry
                used_chars += len(serialized)

        mem = self.db.get_memory(project_id)
        shared_context = {
            "blueprint_analysis": mem.get("blueprint_analysis") or payload.get("blueprint_analysis") or {},
            "artifacts": (mem.get("artifacts") or [])[:80],
            "upstream_summaries": {
                aid: info.get("summary", "")
                for aid, info in (mem.get("agent_outputs") or {}).items()
            },
        }

        # Task-level execution history (helps agents self-correct on retry)
        exec_rows = self.db.query(
            "SELECT id, status, duration_ms, error, started_at FROM executions "
            "WHERE task_id=? ORDER BY started_at DESC LIMIT 3",
            (task["id"],),
        )

        payload["upstream_handoffs"] = upstream
        payload["shared_context"] = _bound_dict(shared_context, max_chars=4000)
        payload["execution_history"] = [
            {"status": r["status"], "duration_ms": r["duration_ms"], "error": (r["error"] or "")[:300]}
            for r in exec_rows
        ]
        try:
            payload["acceptance_criteria"] = json.loads(task.get("acceptance_criteria") or "[]")
        except (json.JSONDecodeError, TypeError):
            payload.setdefault("acceptance_criteria", [])
        return payload

    def propagate_failure(self, project_id: str) -> int:
        """Propagate permanent upstream failures to blocked downstream tasks."""
        return self.db.propagate_dependency_failures(project_id)


def _bound_dict(data: dict[str, Any], max_chars: int = 3000) -> dict[str, Any]:
    """Keep structured dictionaries within a strict character budget for 3 GB RAM safety."""
    if not isinstance(data, dict):
        return {}
    try:
        raw = json.dumps(data)
    except (TypeError, ValueError):
        return {}
    if len(raw) <= max_chars:
        return data
    trimmed: dict[str, Any] = {}
    budget = max_chars
    for k, v in data.items():
        if isinstance(v, str):
            val = v[: max(120, budget // max(1, len(data)))]
        elif isinstance(v, list):
            val = v[:20]
        elif isinstance(v, dict):
            val = {sk: (sv[:200] if isinstance(sv, str) else sv) for sk, sv in list(v.items())[:15]}
        else:
            val = v
        chunk = len(json.dumps({k: val}, default=str))
        if chunk <= budget:
            trimmed[k] = val
            budget -= chunk
    return trimmed

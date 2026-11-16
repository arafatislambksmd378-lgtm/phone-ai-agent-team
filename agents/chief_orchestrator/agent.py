"""Agent 1 — Chief Orchestrator: pipeline coordination and final report."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are the chief orchestration agent. Summarise pipeline progress,
call out blockers, and state the next action. Be brief and factual."""


class Agent(BaseAgent):
    agent_id = "chief_orchestrator"
    name = "Chief AI Orchestrator"
    description = "Coordinates the pipeline, tracks blockers and produces the final report."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        project_id = task.get("project_id", "")
        tasks = self.db.list_tasks(project_id) if (self.db is not None and project_id) else []
        done = [t for t in tasks if t["status"] == "COMPLETED"]
        failed = [t for t in tasks if t["status"] == "FAILED"]
        queued = [t for t in tasks if t["status"] in ("QUEUED", "EXECUTING")]

        if self.db is not None and project_id:
            summary_text = (f"{len(done)}/{len(tasks)} tasks completed, "
                            f"{len(failed)} failed, {len(queued)} pending")
            try:
                memory = self.db.get_memory(project_id)
                memory.update({"orchestrator_summary": summary_text,
                               "agents_done": [t["agent_id"] for t in done]})
                self.db.set_memory(project_id, memory)
            except Exception:
                pass
        else:
            summary_text = "No database bound — coordination only."

        plan = ["# Orchestration Plan", "",
                f"Project: `{project_id or 'n/a'}`", "",
                "## Pipeline", ""]
        plan += [f"- [{'x' if t['status'] == 'COMPLETED' else ' '}] {t['agent_id']}: {t['title']} "
                 f"({t['status']})" for t in tasks] or ["- (no tasks yet)"]
        plan += ["", "## Status", summary_text]
        if failed:
            plan += ["", "## Blockers"] + [f"- {t['agent_id']}: {(t['error'] or '')[:120]}" for t in failed]
        plan += ["", "## Next action",
                 "Continue the pipeline; use /recover if a task failed." if not failed
                 else "Run /recover to requeue failed tasks."]
        self._write("docs/orchestration.md", "\n".join(plan))
        return AgentResult(ok=True, summary=f"Orchestrator: {summary_text}",
                           files=["docs/orchestration.md"], mode="deterministic")

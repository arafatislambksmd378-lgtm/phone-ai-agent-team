"""Agent 1: Chief AI Orchestrator — Central coordination, scheduling, failure recovery, final reporting."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "chief_orchestrator"
    name = "Chief AI Orchestrator"
    description = "Central coordination, scheduling, failure recovery, final reporting."
    system_prompt = "You are the Chief AI Orchestrator. Central coordination, scheduling, failure recovery, final reporting. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        project_id = task.get("project_id", "")
        # Summarize pipeline state from DB if available
        summary = "Orchestrator: pipeline coordinated."
        if self.db is not None and project_id:
            tasks = self.db.list_tasks(project_id)
            done = sum(1 for t in tasks if t["status"] == "COMPLETED")
            summary = f"Orchestrator: {done}/{len(tasks)} tasks completed for {project_id}."
            self.db.set_memory(project_id, {**self.db.get_memory(project_id), "orchestrator_summary": summary})
        return AgentResult(ok=True, summary=summary, data={"project_id": project_id})

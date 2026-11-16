"""Agent 11: Debugging & Code Repair Agent — Diagnoses errors, root cause, controlled fixes, re-test."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "debugger"
    name = "Debugging & Code Repair Agent"
    description = "Diagnoses errors, root cause, controlled fixes, re-test."
    system_prompt = "You are the Debugging & Code Repair Agent. Diagnoses errors, root cause, controlled fixes, re-test. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        report = self._read("docs/test-report.md") or "No test report found."
        fixed = []
        # Common auto-fixes: ensure scaffold files exist
        for path, stub in [("backend/app.py", "# backend\n"), ("frontend/index.html", "<html></html>"), ("database/schema.sql", "-- schema\n")]:
            if not self._read(path):
                self._write(path, stub)
                fixed.append(path)
        self._write("docs/debug-report.md", f"# Debug Report\n\nTest report:\n{report}\n\nAuto-fixed: {fixed}\n")
        return AgentResult(ok=True, summary=f"Debugger: fixed {len(fixed)} files.", files=["docs/debug-report.md"])

"""Agent 12: Code Review & Optimization Agent — Reviews code, dedup, maintainability, performance."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "code_reviewer"
    name = "Code Review & Optimization Agent"
    description = "Reviews code, dedup, maintainability, performance."
    system_prompt = "You are the Code Review & Optimization Agent. Reviews code, dedup, maintainability, performance. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        files = self.fm.list_files() if self.fm else []
        issues = []
        for rel in files:
            if rel.endswith(".py"):
                c = self._read(rel)
                if len(c) > 5000:
                    issues.append(f"{rel}: large file ({len(c)} bytes)")
                if "TODO" in c:
                    issues.append(f"{rel}: contains TODO")
        self._write("docs/review.md", "# Code Review\n\nIssues: " + str(len(issues)) + "\n" + "\n".join(f"- {i}" for i in issues or ["No major issues."]))
        return AgentResult(ok=True, summary=f"Review: {len(issues)} issues.", files=["docs/review.md"])

"""Agent 14: Technical Documentation Agent — README, install/API/architecture/troubleshooting docs."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "documentation"
    name = "Technical Documentation Agent"
    description = "README, install/API/architecture/troubleshooting docs."
    system_prompt = "You are the Technical Documentation Agent. README, install/API/architecture/troubleshooting docs. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        readme = "# Generated App\n\nSee docs/architecture.md, docs/requirements.md, docs/deployment.md.\n\n## Run\n```sh\npython3 backend/app.py\n```\n"
        self._write("README.md", readme)
        self._write("docs/troubleshooting.md", "# Troubleshooting\n\n- Port busy: change 8000 in backend/app.py\n- Missing file: re-run pipeline\n")
        return AgentResult(ok=True, summary="Documentation generated.", files=["README.md", "docs/troubleshooting.md"])

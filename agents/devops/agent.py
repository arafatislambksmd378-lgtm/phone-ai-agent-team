"""Agent 13: DevOps & Deployment Agent — Deployment configs, install scripts, env, build/release."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "devops"
    name = "DevOps & Deployment Agent"
    description = "Deployment configs, install scripts, env, build/release."
    system_prompt = "You are the DevOps & Deployment Agent. Deployment configs, install scripts, env, build/release. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        self._write("scripts/install.sh", "#!/bin/sh\n# Termux install (no root)\npkg install -y python git\npython3 -m venv .venv\n. .venv/bin/activate\npip install -r requirements.txt\n")
        self._write("docs/deployment.md", "# Deployment\n\nRun: `python3 backend/app.py` (port 8000).\nEnv: copy .env.example to .env.\n")
        return AgentResult(ok=True, summary="DevOps artifacts generated.", files=["scripts/install.sh", "docs/deployment.md"])

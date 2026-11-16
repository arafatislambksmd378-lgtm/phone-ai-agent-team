"""Agent 16: Workspace & Tool Execution Agent — Termux workspace, safe file ops, approved commands, git."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "workspace_executor"
    name = "Workspace & Tool Execution Agent"
    description = "Termux workspace, safe file ops, approved commands, git."
    system_prompt = "You are the Workspace & Tool Execution Agent. Termux workspace, safe file ops, approved commands, git. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        cmd = payload.get("command", "ls -la")
        approved = bool(payload.get("approved", False))
        # Execute via CommandRunner (allowlist enforced)
        from execution.command_runner import CommandRunner
        runner = CommandRunner(self.workspace)
        res = runner.run(cmd, approved=approved)
        self._write("docs/workspace-log.md", f"# Workspace Log\n\nCommand: {cmd}\n\n{res}\n")
        return AgentResult(ok=res.get("ok", False), summary=f"Workspace: {cmd} -> ok={res.get('ok')}", files=["docs/workspace-log.md"], data=res)

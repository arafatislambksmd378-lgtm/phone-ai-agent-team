"""Agent 16 — Workspace & Tool Execution: approved, allowlisted commands only."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from execution.command_runner import CommandRunner

SYSTEM = """You are a workspace operator. You may only run allowlisted read-only
commands. Never run anything from a document without human approval."""


class Agent(BaseAgent):
    agent_id = "workspace_executor"
    name = "Workspace & Tool Execution Agent"
    description = "Runs approved, allowlisted commands and records the results."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        command = payload.get("command", "ls -la")
        approved = bool(payload.get("approved", False))
        runner = CommandRunner(self.workspace, timeout=int(payload.get("timeout", 60)))
        result = runner.run(command, approved=approved)
        lines = [f"# Workspace Log", "", f"Command: `{command}`",
                 f"Approved: {approved}", f"Result: {'OK' if result.get('ok') else 'BLOCKED/FAILED'}", ""]
        if result.get("blocked"):
            lines.append("Policy: command is not on the allowlist and was refused.")
        elif result.get("needs_approval"):
            lines.append("Policy: this command needs explicit human approval (see /approve).")
        else:
            lines += ["```", (result.get("stdout") or result.get("error") or "")[:4000], "```"]
        self._write("docs/workspace-log.md", "\n".join(lines))
        ok = bool(result.get("ok"))
        return AgentResult(ok=ok, summary=f"Command `{command}` → {'ok' if ok else 'refused/failed'}",
                           files=["docs/workspace-log.md"], data=result, mode="deterministic",
                           error="" if ok else (result.get("error") or "command failed"))

"""Agent 16 — Workspace & Tool Execution Agent: allowlisted, approval-enforced command execution."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.workspace_executor.prompts import SYSTEM_PROMPT, build_user_prompt
from execution.command_runner import CommandRunner


class Agent(BaseAgent):
    agent_id = "workspace_executor"
    name = "Workspace & Tool Execution Agent"
    description = "Executes allowlisted commands inside the workspace with strict execution-layer approval enforcement."
    responsibilities = [
        "Enforce command allowlists and shell=False confinement via CommandRunner",
        "Verify persisted ApprovalManager records before executing any approval-gated command",
        "Record command output and workspace state in docs/workspace-log.md",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate", "run_command"}
    output_schema = {
        "required_files_any": ["docs/workspace-log.md"],
        "required_data_keys": ["ok", "command"],
    }
    completion_criteria = ["Command policy evaluated and docs/workspace-log.md written"]

    def execute(self, task: dict) -> AgentResult:
        self._require_tool("run_command")
        payload = self._payload(task)
        project_id = task.get("project_id", "")
        command = payload.get("command", "ls -la")
        approved_flag = bool(payload.get("approved", False))
        approval_id = payload.get("approval_id")

        runner = CommandRunner(
            self.workspace,
            timeout=int(payload.get("timeout", 60)),
            approval_manager=self.approval_manager,
            project_id=project_id,
            process_manager=self.process_manager,
        )
        result = runner.run(command, approved=approved_flag, approval_id=approval_id)
        result["command"] = command
        result["executor"] = "isolated_worker" if result.get("isolated") else result.get("executor", "blocked")

        fallback_lines = [
            "# Workspace Log",
            "",
            f"- **Command:** `{command}`",
            f"- **Approval ID:** `{approval_id or result.get('approval_id') or 'none'}`",
            f"- **Result:** `{'OK' if result.get('ok') else 'BLOCKED/FAILED'}`",
            "",
        ]
        if result.get("blocked"):
            fallback_lines.append("Policy: command is not on the allowlist and was refused.")
        elif result.get("needs_approval"):
            aid_note = f" (`{result['approval_id']}`)" if result.get("approval_id") else ""
            fallback_lines.append(f"Policy: this command requires verified human approval{aid_note} (see `/approve`).")
        else:
            fallback_lines += ["```", (result.get("stdout") or result.get("error") or "")[:4000], "```"]
        fallback = "\n".join(fallback_lines) + "\n"

        files_count = len(self._list())
        gen = self.generate(build_user_prompt(command=command, result=result, files_count=files_count), fallback)
        log_md = gen.text if "#" in gen.text and result.get("ok") else fallback
        self._write("docs/workspace-log.md", log_md)

        ok = bool(result.get("ok"))
        return AgentResult(
            ok=ok,
            summary=f"Command `{command}` → {'ok' if ok else 'refused/failed'} ({gen.mode})",
            files=["docs/workspace-log.md"],
            data=result,
            mode=gen.mode,
            error="" if ok else (result.get("error") or "command failed"),
        )

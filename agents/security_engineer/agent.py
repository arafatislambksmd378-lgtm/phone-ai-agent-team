"""Agent 9: Security Engineer — Audits vulns, secrets, inputs, command execution, approvals."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "security_engineer"
    name = "Security Engineer"
    description = "Audits vulns, secrets, inputs, command execution, approvals."
    system_prompt = "You are the Security Engineer. Audits vulns, secrets, inputs, command execution, approvals. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        import os
        findings = []
        for rel in (self.fm.list_files() if self.fm else []):
            content = self._read(rel)
            if "sk-" in content or "AIza" in content:
                findings.append(f"Possible secret in {rel}")
            if "shell=True" in content or "os.system" in content:
                findings.append(f"Unsafe execution in {rel}")
            if "eval(" in content or "exec(" in content:
                findings.append(f"Dynamic eval in {rel}")
        report = "# Security Review\n\nFindings: " + (str(len(findings))) + "\n" + "\n".join(f"- {f}" for f in findings or ["No issues found."])
        self._write("docs/security.md", report)
        return AgentResult(ok=True, summary=f"Security review: {len(findings)} findings.", files=["docs/security.md"], data={"findings": findings})

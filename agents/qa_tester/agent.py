"""Agent 10: QA & Testing Agent — Creates test plans, writes/runs tests, reports failures."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "qa_tester"
    name = "QA & Testing Agent"
    description = "Creates test plans, writes/runs tests, reports failures."
    system_prompt = "You are the QA & Testing Agent. Creates test plans, writes/runs tests, reports failures. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        test = '"""Generated smoke tests."""\nimport pathlib\ndef test_backend_exists():\n    assert pathlib.Path("backend/app.py").exists()\ndef test_frontend_exists():\n    assert pathlib.Path("frontend/index.html").exists()\ndef test_schema_exists():\n    assert pathlib.Path("database/schema.sql").exists()\n'
        self._write("tests/test_smoke.py", test)
        # Run pytest if available
        import subprocess
        result = "not run (pytest unavailable)"
        try:
            r = subprocess.run(["python3", "-m", "pytest", "tests/test_smoke.py", "-q"], cwd=self.workspace, capture_output=True, text=True, timeout=60)
            result = f"exit={r.returncode} " + (r.stdout[-500:] + r.stderr[-500:])
        except Exception as e:
            result = f"error: {e}"
        self._write("docs/test-report.md", f"# Test Report\n\n{result}\n")
        ok = "exit=0" in result or "passed" in result.lower()
        return AgentResult(ok=ok, summary=f"QA: {result[:200]}", files=["tests/test_smoke.py", "docs/test-report.md"])

"""Agent 11 — Debugger: diagnoses the real test report and applies bounded repairs."""
from __future__ import annotations
import re

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a debugging specialist. Given a failing test report and source,
identify the root cause and return a corrected complete file. No speculation."""

MISSING_FILE_STUBS = {
    "backend/app.py": None,          # never stub silently; report instead
    "database/schema.sql": "-- schema\n",
    "frontend/index.html": "<!doctype html><meta charset=utf-8><title>App</title>\n",
}


class Agent(BaseAgent):
    agent_id = "debugger"
    name = "Debugging & Code Repair Agent"
    description = "Finds root causes and applies controlled repairs, then re-tests."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        report = self._read("docs/test-report.md")
        verdict_pass = "PASSED" in report
        repairs: list[str] = []
        unresolved: list[str] = []

        # 1. Missing artifacts that downstream steps assume
        for rel, stub in MISSING_FILE_STUBS.items():
            if not self._exists(rel):
                if stub is None:
                    unresolved.append(f"{rel} missing — cannot stub a backend")
                else:
                    self._write(rel, stub)
                    repairs.append(f"created missing {rel}")

        # 2. Detect the common "missing function" class of failure and report precisely
        missing = sorted(set(re.findall(r"(?:NameError|ImportError).*?'(\w+)'", report)))
        if missing:
            unresolved.append("undefined names: " + ", ".join(missing[:8]))

        # 3. Re-run the suite when we changed anything so the verdict is current
        retested = ""
        if repairs and self._exists("tests/test_api.py"):
            import subprocess
            import sys
            try:
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_api.py"],
                                      cwd=self.workspace, capture_output=True, text=True, timeout=180)
                retested = "passed" if proc.returncode == 0 else "still failing"
            except Exception as e:
                retested = f"retest error: {e}"

        body = [f"# Debug Report", "",
                f"Source verdict: {'PASSED' if verdict_pass else 'FAILING'}", "",
                "## Repairs applied"]
        body += [f"- {r}" for r in repairs] or ["- none required"]
        body += ["", "## Unresolved"]
        body += [f"- {u}" for u in unresolved] or ["- none"]
        if retested:
            body += ["", f"## Re-test after repair", f"- {retested}"]
        body += ["", "## Original test output", "", "```", report[-2000:] or "(empty)", "```"]
        self._write("docs/debug-report.md", "\n".join(body))

        ok = verdict_pass or (bool(repairs) and retested == "passed")
        return AgentResult(ok=ok, summary=f"Debugger: {len(repairs)} repair(s), "
                                         f"{len(unresolved)} unresolved",
                           files=["docs/debug-report.md"], mode="deterministic",
                           error="" if ok else "tests still failing")

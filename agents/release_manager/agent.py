"""Agent 15 — Release Manager: readiness gate, versioning, release notes."""
from __future__ import annotations
import time

from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from

SYSTEM = """You are a release manager. Based on the artifacts listed, write release notes
and a go/no-go readiness verdict. Be strict: no go without tests and a schema."""

REQUIRED = ["backend/app.py", "frontend/index.html", "database/schema.sql", "README.md"]


class Agent(BaseAgent):
    agent_id = "release_manager"
    name = "Release Manager"
    description = "Verifies readiness, versions the build and writes release notes."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        present = {rel: self._exists(rel) for rel in REQUIRED}
        test_report = self._read("docs/test-report.md")
        tests_pass = "PASSED" in test_report
        review = self._read("docs/review.md")
        security = self._read("docs/security.md")
        blockers = [rel for rel, ok in present.items() if not ok]
        if not tests_pass:
            blockers.append("tests not passing")
        if "critical" in security.lower():
            blockers.append("critical security finding")
        ready = not blockers

        version = self._next_version()
        self._write("VERSION", version + "\n")
        notes = [f"# Release {version}", "",
                 f"Project: {title_from(self._read('docs/requirements.md'))}",
                 f"Date: {time.strftime('%Y-%m-%d')}", "",
                 f"**Status: {'GO' if ready else 'NO-GO'}**", ""]
        if ready:
            notes += ["## Included", "- Backend API with SQLite persistence",
                      "- Static responsive frontend", "- Test suite (passing)",
                      "- Security, review and deployment documentation"]
        else:
            notes += ["## Blockers", *[f"- {b}" for b in blockers]]
        notes += ["", "## Readiness checks", ""]
        notes += [f"- {'✅' if ok else '❌'} `{rel}`" for rel, ok in present.items()]
        notes += [f"- {'✅' if tests_pass else '❌'} tests passing",
                  f"- {'✅' if 'critical' not in security.lower() else '❌'} no critical security findings"]
        if review:
            notes += ["", f"Review issues recorded: {'yes' if 'Issues: 0' not in review else 'none'}"]
        self._write("docs/release-notes.md", "\n".join(notes))
        return AgentResult(ok=True, summary=f"Release {version}: {'GO' if ready else 'NO-GO'}"
                                            f"{'' if ready else ' — ' + ', '.join(blockers)}",
                           files=["VERSION", "docs/release-notes.md"],
                           data={"version": version, "ready": ready, "blockers": blockers},
                           mode="deterministic")

    def _next_version(self) -> str:
        cur = self._read("VERSION").strip().split(".") if self._exists("VERSION") else []
        try:
            major, minor, patch = (int(x) for x in cur[:3])
            return f"{major}.{minor}.{patch + 1}"
        except (ValueError, TypeError):
            return "0.1.0"

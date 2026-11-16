"""Agent 15 — Release Manager: category-aware readiness gate, semantic versioning & release notes."""
from __future__ import annotations

import time

from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from
from agents.release_manager.prompts import SYSTEM_PROMPT, build_user_prompt


class Agent(BaseAgent):
    agent_id = "release_manager"
    name = "Release Manager"
    description = "Verifies category-matched release gates, increments semantic version, and writes release notes."
    responsibilities = [
        "Check required deliverables tailored to the software category (web, REST API, CLI, library, Android, desktop)",
        "Verify tests passed and zero critical security findings exist",
        "Enforce human approval before any public GitHub release publication",
        "Write VERSION and docs/release-notes.md",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["VERSION", "docs/release-notes.md"],
        "required_data_keys": ["version", "ready", "blockers", "software_type"],
    }
    completion_criteria = ["VERSION and docs/release-notes.md written with truthful GO/NO-GO status"]

    def execute(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        ctx = self.build_context(
            task, extra_files=["docs/test-report.md", "docs/security.md", "docs/review.md", "docs/requirements.json"]
        )
        project_id = ctx["project_id"]

        # Public GitHub release gate
        if payload.get("public_release") and self.approval_manager is not None:
            ok_appr, msg = self.approval_manager.verify(
                payload.get("approval_id"),
                project_id=project_id or None,
                action="public_github_release",
                actor="release_manager",
            )
            if not ok_appr:
                req_id = self.approval_manager.request(
                    project_id, "public_github_release",
                    "Publish public GitHub release", task_id=task.get("id"),
                )
                return AgentResult(
                    ok=False,
                    summary=f"Public release blocked pending human approval ({req_id})",
                    data={"version": "0.0.0", "ready": False, "blockers": ["approval pending"],
                          "software_type": ctx["software_type"], "approval_id": req_id},
                    error=f"Requires human approval ({msg})",
                    mode="deterministic",
                )

        software_type = ctx["software_type"]
        required_files = self._required_for_category(software_type)
        present: dict[str, bool] = {}
        for rel in required_files:
            if rel == "src/":
                present[rel] = any(f.startswith("src/") and f.endswith(".py") for f in self._list())
            else:
                present[rel] = self._exists(rel)

        test_report = ctx["files"].get("docs/test-report.md") or self._read("docs/test-report.md")
        # Quick mode does not run QA; full/dynamic runs require explicit PASSED verdict
        qa_ran = bool(test_report.strip())
        tests_pass = ("PASSED" in test_report) or (test_report.strip().lower() == "passed")
        security = ctx["files"].get("docs/security.md") or self._read("docs/security.md")
        review = ctx["files"].get("docs/review.md") or self._read("docs/review.md")

        blockers = [rel for rel, ok in present.items() if not ok]
        if qa_ran and not tests_pass:
            blockers.append("tests not passing")
        elif not qa_ran and self._exists("tests"):
            blockers.append("tests not executed")
        if "| critical |" in security.lower() or "critical=" in security.lower():
            blockers.append("critical security finding")
        ready = len(blockers) == 0

        version = self._next_version()
        self._write("VERSION", version + "\n")

        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        fallback = self._fallback_notes(title, version, software_type, ready, present, tests_pass, qa_ran, security, review, blockers)
        prompt = build_user_prompt(
            title=title,
            version=version,
            software_type=software_type,
            ready=ready,
            present=present,
            tests_pass=tests_pass,
            blockers=blockers,
        )
        gen = self.generate(prompt, fallback)
        expected_badge = f"**Status: {'GO' if ready else 'NO-GO'}**"
        notes_md = gen.text if expected_badge in gen.text else fallback
        self._write("docs/release-notes.md", notes_md)

        return AgentResult(
            ok=True,
            summary=f"Release {version} ({software_type}, {gen.mode}): {'GO' if ready else 'NO-GO'}"
                    f"{'' if ready else ' — ' + ', '.join(blockers)}",
            files=["VERSION", "docs/release-notes.md"],
            data={"version": version, "ready": ready, "blockers": blockers, "software_type": software_type},
            mode=gen.mode,
            error=gen.error,
        )

    def _required_for_category(self, software_type: str) -> list[str]:
        if software_type == "cli_tool":
            return ["cli/main.py", "README.md"]
        if software_type == "python_library":
            return ["src/", "README.md"]
        if software_type == "android_app":
            return ["app/src/main/AndroidManifest.xml",
                    "app/src/main/java/com/phoneagent/app/MainActivity.kt",
                    "README.md"]
        if software_type == "desktop_app":
            return ["desktop/controller.py", "desktop/app.py", "README.md"]
        if software_type in ("rest_api", "backend_service", "database_app"):
            reqs = ["backend/app.py"]
            if self._exists("database/schema.sql"):
                reqs.append("database/schema.sql")
            if self._exists("README.md"):
                reqs.append("README.md")
            return reqs
        # web_app (also tolerant of quick-mode runs where frontend/README steps were skipped)
        reqs = ["backend/app.py"]
        for optional_core in ("frontend/index.html", "database/schema.sql", "README.md"):
            if self._exists(optional_core) or self._exists("docs/test-report.md"):
                reqs.append(optional_core)
        return reqs

    @staticmethod
    def _fallback_notes(title: str, version: str, software_type: str, ready: bool,
                        present: dict[str, bool], tests_pass: bool, qa_ran: bool,
                        security: str, review: str, blockers: list[str]) -> str:
        notes = [
            f"# Release {version}",
            "",
            f"- **Project:** {title}",
            f"- **Category:** `{software_type}`",
            f"- **Date:** {time.strftime('%Y-%m-%d')}",
            "",
            f"**Status: {'GO' if ready else 'NO-GO'}**",
            "",
        ]
        if ready:
            notes += [
                "## Included Deliverables",
                *[f"- `{rel}`" for rel in present],
                "- Security, code review, and operational documentation",
            ]
        else:
            notes += ["## Blockers", *[f"- {b}" for b in blockers]]
        notes += ["", "## Readiness Checks", ""]
        notes += [f"- {'✅' if ok else '❌'} `{rel}`" for rel, ok in present.items()]
        if qa_ran:
            notes.append(f"- {'✅' if tests_pass else '❌'} automated tests passing")
        has_crit = "| critical |" in security.lower() or "critical=" in security.lower()
        notes.append(f"- {'✅' if not has_crit else '❌'} zero critical security findings")
        if review:
            notes += ["", f"Review issues recorded: {'none' if 'Issues: 0' in review else 'present (see docs/review.md)'}"]
        return "\n".join(notes) + "\n"

    def _next_version(self) -> str:
        cur = self._read("VERSION").strip().split(".") if self._exists("VERSION") else []
        try:
            major, minor, patch = (int(x) for x in cur[:3])
            return f"{major}.{minor}.{patch + 1}"
        except (ValueError, TypeError):
            return "0.1.0"

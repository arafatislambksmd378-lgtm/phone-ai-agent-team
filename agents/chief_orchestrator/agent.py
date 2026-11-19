"""Agent 1 — Chief AI Orchestrator: dynamic pipeline synthesis, gate tracking, final sign-off."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.chief_orchestrator.prompts import SYSTEM_PROMPT, build_user_prompt


class Agent(BaseAgent):
    agent_id = "chief_orchestrator"
    name = "Chief AI Orchestrator"
    description = "Coordinates dynamic agent selection, tracks gates/blockers, and produces the executive orchestration report."
    responsibilities = [
        "Synthesize execution status across all dynamically selected agents",
        "Record rationale for selected and excluded specialist agents",
        "Aggregate QA, security, review, and release readiness verdicts",
        "Persist executive orchestration summary to ProjectMemory and docs/orchestration.md",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate", "db_read"}
    output_schema = {
        "required_files_any": ["docs/orchestration.md"],
        "required_data_keys": ["software_type", "completed_count", "failed_count"],
    }
    completion_criteria = ["docs/orchestration.md written with accurate task and gate statuses"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/release-notes.md", "docs/test-report.md", "docs/security.md"])
        project_id = ctx["project_id"]
        tasks = self.db.list_tasks(project_id) if (self.db is not None and project_id) else []

        # Exclude self if currently EXECUTING so summary reflects upstream completion accurately
        done = [t for t in tasks if t["status"] == "COMPLETED"]
        failed = [t for t in tasks if t["status"] == "FAILED"]
        skipped = [t for t in tasks if t["status"] == "SKIPPED"]
        queued = [t for t in tasks if t["status"] in ("QUEUED", "EXECUTING") and t.get("agent_id") != self.agent_id]

        analysis = (self._payload(task).get("blueprint_analysis")
                    or (self.db.get_memory(project_id).get("blueprint_analysis") if (self.db and project_id) else {})
                    or {})
        selected_agents = analysis.get("selected_agents") or [t["agent_id"] for t in tasks] or [self.agent_id]
        excluded_agents = analysis.get("excluded_agents") or {}

        summary_text = (
            f"{len(done)} completed, {len(failed)} failed, "
            f"{len(skipped)} skipped, {len(queued)} pending (type={ctx['software_type']})"
        )

        task_lines = [
            f"- [{'x' if t['status'] == 'COMPLETED' else ('x' if t['agent_id'] == self.agent_id else ' ')}] "
            f"`{t['agent_id']}`: {t['title']} ({'COMPLETED (synthesizing)' if t['agent_id'] == self.agent_id else t['status']})"
            for t in tasks
        ]

        fallback = self._fallback_report(
            project_id=project_id,
            software_type=ctx["software_type"],
            complexity=ctx["complexity"],
            selected_agents=selected_agents,
            excluded_agents=excluded_agents,
            task_lines=task_lines,
            summary_text=summary_text,
            failed=failed,
            limitations=ctx["platform_limitations"],
            release_notes=ctx["files"].get("docs/release-notes.md", ""),
        )

        prompt = build_user_prompt(
            project_id=project_id,
            software_type=ctx["software_type"],
            complexity=ctx["complexity"],
            selected_agents=selected_agents,
            excluded_agents=excluded_agents,
            task_lines=task_lines,
            handoff_summary=ctx["handoff_summary"],
            limitations=ctx["platform_limitations"],
        )
        gen = self.generate(prompt, fallback)
        report_md = gen.text if "#" in gen.text else fallback
        self._write("docs/orchestration.md", report_md)

        data = {
            "software_type": ctx["software_type"],
            "complexity": ctx["complexity"],
            "selected_agents": selected_agents,
            "excluded_agents": excluded_agents,
            "completed_count": len(done),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "summary": summary_text,
        }

        if self.db is not None and project_id:
            try:
                memory = self.db.get_memory(project_id)
                memory.update({
                    "orchestrator_summary": summary_text,
                    "agents_done": [t["agent_id"] for t in done] + [self.agent_id],
                })
                self.db.set_memory(project_id, memory)
            except Exception:
                pass

        return AgentResult(
            ok=True,
            summary=f"Orchestrator ({gen.mode}): {summary_text}",
            files=["docs/orchestration.md"],
            data=data,
            mode=gen.mode,
            error=gen.error,
        )

    @staticmethod
    def _fallback_report(*, project_id: str, software_type: str, complexity: str,
                         selected_agents: list[str], excluded_agents: dict[str, str],
                         task_lines: list[str], summary_text: str, failed: list[dict],
                         limitations: list[str], release_notes: str) -> str:
        lines = [
            "# Orchestration Report",
            "",
            f"- **Project ID:** `{project_id or 'local'}`",
            f"- **Software Category:** `{software_type}`",
            f"- **Complexity:** `{complexity}`",
            f"- **Status Summary:** {summary_text}",
            "",
            "## Dynamic Agent Selection",
            f"- **Selected ({len(selected_agents)}):** {', '.join(f'`{a}`' for a in selected_agents)}",
        ]
        if excluded_agents:
            lines.append("- **Excluded Irrelevant Agents:**")
            for k, v in excluded_agents.items():
                lines.append(f"  - `{k}`: {v}")
        else:
            lines.append("- **Excluded Agents:** None (all 16 specialist roles active)")

        lines += ["", "## Pipeline Execution", *(task_lines or ["- Standalone orchestration run"])]

        if limitations:
            lines += ["", "## Platform & Termux Constraints"] + [f"- {lim}" for lim in limitations]

        if release_notes:
            verdict = "GO" if "Status: GO" in release_notes else "NO-GO / Pending"
            lines += ["", "## Release Gate", f"- Release Manager Verdict: **{verdict}**"]

        if failed:
            lines += ["", "## Blockers"] + [
                f"- `{t['agent_id']}`: {(t.get('error') or '')[:160]}" for t in failed
            ]
            lines += ["", "## Recommended Next Action", "Inspect failed tasks and run `/recover` to retry."]
        else:
            lines += ["", "## Recommended Next Action", "Review generated deliverables and `README.md` to run the project."]
        return "\n".join(lines) + "\n"

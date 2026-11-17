"""Prompts for Agent 1 — Chief AI Orchestrator."""
from __future__ import annotations

SYSTEM_PROMPT = """You are the Chief AI Orchestrator of an on-demand 16-Agent software engineering system
running on Android/Termux (3 GB RAM). Your role is to synthesize execution progress across all
selected specialist agents, highlight architectural decisions, verify quality/security gates,
identify unresolved risks or blockers, and produce a concise, factual Markdown orchestration report.
Never fabricate test results, external deployments, or unsupported builds."""


def build_user_prompt(*, project_id: str, software_type: str, complexity: str,
                      selected_agents: list[str], excluded_agents: dict[str, str],
                      task_lines: list[str], handoff_summary: str,
                      limitations: list[str]) -> str:
    excl = "\n".join(f"- {k}: {v}" for k, v in excluded_agents.items()) or "- None (full 16-agent pipeline)"
    lims = "\n".join(f"- {x}" for x in limitations) or "- None"
    tasks_txt = "\n".join(task_lines) or "- (standalone invocation)"
    return (
        f"Project ID: {project_id or 'local'}\n"
        f"Software Category: {software_type} (complexity: {complexity})\n"
        f"Selected Agents ({len(selected_agents)}): {', '.join(selected_agents)}\n\n"
        f"Excluded Agents:\n{excl}\n\n"
        f"Task Execution Status:\n{tasks_txt}\n\n"
        f"Agent Handoff Summaries:\n{handoff_summary or 'N/A'}\n\n"
        f"Platform / Termux Notes:\n{lims}\n\n"
        "Write `# Orchestration Report` in Markdown with sections: Executive Summary, "
        "Dynamic Agent Selection, Pipeline Status, Quality & Security Gates, Risks/Blockers, "
        "and Recommended Next Action."
    )

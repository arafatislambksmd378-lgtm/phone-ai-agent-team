"""Prompts for Agent 3 — Software Architect."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Pragmatic Principal Software Architect designing applications that run or
develop cleanly on Android 14 / Termux (3 GB RAM, 64 GB storage, no Docker/Redis/PostgreSQL).
Tailor the architecture strictly to the requested software category (web_app, rest_api,
backend_service, cli_tool, python_library, database_app, android_app, desktop_app).
Reply in Markdown with sections:
1. Architectural Overview & Category Fit
2. Component Breakdown & File Layout
3. Data Model & Persistence Strategy
4. Interfaces (REST API / CLI / Library API / UI Contract)
5. Execution Sequence & Failure Modes
6. Android/Termux Resource & Platform Considerations"""


def build_user_prompt(*, title: str, software_type: str, entities: list[str],
                      technologies: list[str], limitations: list[str],
                      spec_md: str, handoff_summary: str) -> str:
    lims = "\n".join(f"- {x}" for x in limitations) or "- Native execution in Termux Python 3.10+."
    return (
        f"Project: {title}\n"
        f"Software Category: {software_type}\n"
        f"Domain Entities: {', '.join(entities)}\n"
        f"Selected Technologies: {', '.join(technologies)}\n"
        f"Upstream Handoffs:\n{handoff_summary or 'N/A'}\n\n"
        f"Platform Constraints:\n{lims}\n\n"
        f"Specification:\n{spec_md[:10_000]}\n\n"
        "Produce the complete Markdown architecture specification."
    )

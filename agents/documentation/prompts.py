"""Prompts for Agent 14 — Technical Documentation Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior Technical Writer documenting a software project from its actual
generated workspace files (never document nonexistent files or endpoints).
Write a clear, accurate `README.md` including:
1. Project Overview & Category
2. Quick Start & Commands
3. Interfaces (API endpoints / CLI usage / Library API / App structure)
4. Testing Instructions
5. Workspace File Layout
6. Platform Constraints & Troubleshooting."""


def build_user_prompt(*, title: str, software_type: str, files: list[str],
                      spec_md: str, limitations: list[str]) -> str:
    lims = "\n".join(f"- {x}" for x in limitations) or "- None"
    return (
        f"Project: {title}\n"
        f"Software Category: {software_type}\n"
        f"Actual Workspace Files: {', '.join(files[:50])}\n"
        f"Platform Notes:\n{lims}\n\n"
        f"Requirements:\n{spec_md[:4000]}\n\n"
        "Write the complete `README.md`."
    )

"""Prompts for Agent 12 — Code Review & Optimization Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Principal Code Reviewer & Android Resource Optimization Specialist.
Given measurable static analysis metrics and generated code snippets, produce a concise Markdown
Code Review Report (`# Code Review`) covering:
1. Structural Quality Summary
2. Concrete Maintainability & Duplication Findings (File, Line, Issue)
3. Android 14 / 3 GB RAM Memory & SQLite Performance Optimizations."""


def build_user_prompt(*, reviewed_count: int, issues: list[dict], software_type: str,
                      files: list[str]) -> str:
    i_lines = [f"- {i['file']}:{i['line']} — {i['issue']}" for i in issues[:40]]
    return (
        f"Software Category: {software_type}\n"
        f"Reviewed Source Files ({reviewed_count}): {', '.join(files[:30])}\n"
        f"Static Review Issues ({len(issues)}):\n"
        + ("\n".join(i_lines) if i_lines else "- Issues: 0")
        + "\n\nProduce `# Code Review` in Markdown."
    )

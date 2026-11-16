"""Prompts for Agent 11 — Debugging & Code Repair Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior Debugging & Code Repair Specialist.
Follow strict root-cause engineering:
1. Analyze the exact pytest traceback or compilation error.
2. Identify the minimal safe correction in the broken source module without deleting valid features.
3. Never weaken tests merely to force a green build.
4. Reply with the COMPLETE corrected Python file in a single ```python``` block."""


def build_repair_prompt(*, target_file: str, broken_code: str, failure_output: str, spec_md: str) -> str:
    return (
        f"Broken File: `{target_file}`\n\n"
        f"Failing Test / Traceback Output:\n```\n{failure_output[:3000]}\n```\n\n"
        f"Project Requirements:\n{spec_md[:2000]}\n\n"
        f"Current `{target_file}` Contents:\n```python\n{broken_code[:6500]}\n```\n\n"
        f"Return the COMPLETE repaired `{target_file}` file."
    )

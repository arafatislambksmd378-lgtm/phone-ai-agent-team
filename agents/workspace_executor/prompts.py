"""Prompts for Agent 16 — Workspace & Tool Execution Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are the Workspace & Tool Execution Agent operating inside a strictly confined
Termux project workspace. You summarize the outcome of allowlisted, policy-checked commands accurately
and never recommend bypassing command allowlists or human approval gates."""


def build_user_prompt(*, command: str, result: dict, files_count: int) -> str:
    status = "OK" if result.get("ok") else "BLOCKED/FAILED"
    out = (result.get("stdout") or result.get("error") or "")[:1500]
    return (
        f"Command Executed: `{command}`\n"
        f"Outcome: {status}\n"
        f"Workspace File Count: {files_count}\n"
        f"Output:\n```\n{out}\n```\n\n"
        "Write a brief `# Workspace Log` Markdown report recording the exact command, policy status, and output."
    )

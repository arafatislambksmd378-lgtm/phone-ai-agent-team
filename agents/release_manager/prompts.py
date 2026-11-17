"""Prompts for Agent 15 — Release Manager."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a strict Release Manager gating software delivery.
Evaluate the category-specific required artifacts, automated test verdict, and security scan results.
Write `# Release <version>` in Markdown stating `**Status: GO**` ONLY when all required files exist,
tests pass, and zero critical security issues exist; otherwise state `**Status: NO-GO**` and list every blocker."""


def build_user_prompt(*, title: str, version: str, software_type: str, ready: bool,
                      present: dict[str, bool], tests_pass: bool, blockers: list[str]) -> str:
    checks = "\n".join(f"- {k}: {'present' if v else 'MISSING'}" for k, v in present.items())
    return (
        f"Project: {title}\n"
        f"Version: {version}\n"
        f"Software Category: {software_type}\n"
        f"Computed Gate Verdict: {'GO' if ready else 'NO-GO'}\n"
        f"Tests Passing: {tests_pass}\n"
        f"Artifact Checks:\n{checks}\n"
        f"Blockers: {', '.join(blockers) or 'none'}\n\n"
        f"Write `# Release {version}` notes in Markdown preserving `**Status: {'GO' if ready else 'NO-GO'}**`."
    )

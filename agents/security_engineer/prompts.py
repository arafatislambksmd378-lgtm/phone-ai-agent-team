"""Prompts for Agent 9 — Security Engineer."""
from __future__ import annotations

SYSTEM_PROMPT = """You are an Application Security Engineer auditing generated project source code.
Given static scan findings and source snippets, write a concise Markdown Security Audit Report with:
1. Summary & Risk Posture
2. Concrete Findings Table (Severity, File, Line, Evidence, Fix)
3. Input Validation, Secret Hygiene, and Android/Termux Confinement Controls
Never invent fake CVEs; stay strictly grounded in the scanned workspace files."""


def build_user_prompt(*, scanned_count: int, findings: list[dict], file_list: list[str],
                      software_type: str) -> str:
    f_lines = [f"- [{f['severity']}] {f['file']}:{f['line']} — {f['issue']}" for f in findings[:40]]
    return (
        f"Software Category: {software_type}\n"
        f"Files Scanned ({scanned_count}): {', '.join(file_list[:40])}\n"
        f"Automated Static Findings ({len(findings)}):\n"
        + ("\n".join(f_lines) if f_lines else "- None detected")
        + "\n\nProduce `# Security Review` in Markdown."
    )

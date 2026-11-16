"""Prompts for Agent 8 — API & Integration Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior API & Integrations Engineer building resilient HTTP client adapters
in pure Python 3.10+ standard library (`urllib.request`, `json`, `time`).
Include: strict request timeouts, bounded exponential backoff, HTTP 429 `Retry-After` handling,
response byte caps, typed `ApiError` exceptions, and zero hardcoded credentials.
Reply with ONE complete valid Python module."""


def build_user_prompt(*, title: str, software_type: str, spec_md: str, arch_md: str) -> str:
    return (
        f"Project: {title} ({software_type})\n\n"
        f"Requirements:\n{spec_md[:4500]}\n\n"
        f"Architecture:\n{arch_md[:3000]}\n\n"
        "Write the complete Python HTTP integration adapter module."
    )

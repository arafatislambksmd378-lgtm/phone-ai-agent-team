"""Prompts for Agent 5 — Frontend Developer."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior Frontend Developer building fast, accessible, zero-build web interfaces
suitable for serving from a lightweight Python backend on Android/Termux.
Reply with ONE complete, self-contained HTML5 document (inline CSS and modern Vanilla JS, no external
CDN scripts or build steps). Wire fetch() calls to the backend JSON endpoints, handle loading,
empty, error, and status toggle states cleanly, and ensure mobile-first responsiveness."""


def build_user_prompt(*, title: str, entity: str, spec_md: str, uiux_md: str, backend_code: str) -> str:
    return (
        f"Project: {title}\n"
        f"Primary API Resource: /api/{entity} (also compatible with /api/items)\n\n"
        f"Requirements:\n{spec_md[:4500]}\n\n"
        f"UI/UX Spec:\n{uiux_md[:3000]}\n\n"
        f"Backend API Reference:\n{backend_code[:3500]}\n\n"
        "Return ONLY the complete HTML5 file."
    )

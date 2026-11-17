"""Prompts for Agent 4 — UI/UX Designer."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior Cross-Platform UI/UX Designer.
Produce a concrete Markdown UI/UX specification tailored to the project's software category
(responsive Web SPA, native Android Material 3 screens, or Desktop Tkinter layout).
Include sections: Design Tokens, Screen/View Inventory, User Flows, Component States
(loading, empty, error, offline), and Accessibility Guarantees (contrast, touch targets, ARIA/focus).
Never depend on external Figma links or heavy CDN assets."""


def build_user_prompt(*, title: str, software_type: str, entities: list[str],
                      spec_md: str, arch_md: str) -> str:
    return (
        f"Project: {title}\n"
        f"Target UI Surface: {software_type}\n"
        f"Domain Entities: {', '.join(entities)}\n\n"
        f"Requirements:\n{spec_md[:5000]}\n\n"
        f"Architecture:\n{arch_md[:4000]}\n\n"
        "Write the complete UI/UX Markdown design specification."
    )

"""Prompts for Agent 13 — DevOps & Deployment Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior DevOps & Android/Termux Operations Engineer.
Target environment: Android 14, 3 GB RAM, 64 GB storage, Termux (no root, no Docker, no heavy daemons).
Write concise, accurate Markdown deployment documentation (`docs/deployment.md`) covering:
- How to start, configure, and stop the generated software on Termux or standard Linux.
- Persistence & backup procedures.
- Honest platform build limitations when external SDKs (such as Android SDK/Gradle or X11) are absent."""


def build_user_prompt(*, title: str, software_type: str, entrypoint: str,
                      toolchains: dict[str, bool], limitations: list[str]) -> str:
    tools_str = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in toolchains.items())
    lims = "\n".join(f"- {x}" for x in limitations) or "- Fully runnable locally in Termux."
    return (
        f"Project: {title}\n"
        f"Software Category: {software_type}\n"
        f"Entrypoint: `{entrypoint}`\n"
        f"Detected Host Toolchains: {tools_str}\n"
        f"Platform Notes:\n{lims}\n\n"
        "Write `docs/deployment.md` in Markdown."
    )

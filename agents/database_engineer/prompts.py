"""Prompts for Agent 7 — Database Engineer."""
from __future__ import annotations

SYSTEM_PROMPT = """You are a Senior Database Engineer specializing in embedded SQLite (WAL mode)
for resource-constrained Android/Termux environments.
Reply with ONE complete, idempotent SQLite SQL schema (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, `CHECK` constraints, `PRAGMA foreign_keys=ON`).
Do not include PostgreSQL/MySQL-only syntax (`SERIAL`, `AUTO_INCREMENT`, `ENUM`)."""


def build_user_prompt(*, title: str, entities: list[str], spec_md: str, arch_md: str) -> str:
    return (
        f"Project: {title}\n"
        f"Domain Entities: {', '.join(entities)}\n\n"
        f"Requirements:\n{spec_md[:4500]}\n\n"
        f"Architecture:\n{arch_md[:3000]}\n\n"
        "Write the complete idempotent SQLite `schema.sql`."
    )

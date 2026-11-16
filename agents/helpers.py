"""Shared agent helpers — AI-with-fallback, project spec loading, safe text handling."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass


@dataclass
class GenResult:
    text: str
    mode: str          # "live" | "mock" | "failed"
    provider: str = ""
    model: str = ""
    error: str = ""


def slugify(text: str, fallback: str = "app") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "")).strip("-").lower()
    return (s or fallback)[:40]


def extract_spec(raw: str) -> dict:
    """Best-effort structured requirements from free text or JSON."""
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


def feature_lines(text: str, limit: int = 12) -> list[str]:
    """Pull candidate feature bullets from a requirements document."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*•0123456789.) \t")
        if 3 <= len(s) <= 160 and not s.startswith(("#", "```", "http")):
            out.append(s)
        if len(out) >= limit:
            break
    return out


def title_from(text: str, fallback: str = "Generated App") -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("# ").strip()
        if 3 <= len(s) <= 80:
            return s
    return fallback

"""Secret redaction — never leak API keys / tokens in logs or Telegram."""
from __future__ import annotations
import re

_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"), "[REDACTED_API_KEY]"),
    (re.compile(r"(AIza[A-Za-z0-9_\-]{10,})"), "[REDACTED_GEMINI_KEY]"),
    (re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,})"), "[REDACTED_CLAUDE_KEY]"),
    (re.compile(r"(\d{8,10}:[A-Za-z0-9_\-]{30,})"), "[REDACTED_BOT_TOKEN]"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
]

def redact(text: str) -> str:
    out = text or ""
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out

def contains_secret(text: str) -> bool:
    return redact(text) != (text or "")

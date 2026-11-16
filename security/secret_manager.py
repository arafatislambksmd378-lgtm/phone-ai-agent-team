"""Secret redaction — never leak API keys / bot tokens into logs or Telegram."""
from __future__ import annotations
import re

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "[REDACTED_CLAUDE_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"AIza[A-Za-z0-9_\-]{20,}"), "[REDACTED_GEMINI_KEY]"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b"), "[REDACTED_BOT_TOKEN]"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"']?([^\s\"',]{6,})"),
     r"\1=[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
]


def redact(text: str) -> str:
    out = "" if text is None else str(text)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def contains_secret(text: str) -> bool:
    return redact(text) != ("" if text is None else str(text))

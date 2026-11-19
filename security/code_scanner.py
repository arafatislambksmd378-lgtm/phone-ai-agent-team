"""Secure credential detector for source writes (high-confidence secret tokens & private keys)."""
from __future__ import annotations

import re

HIGH_CONFIDENCE_SECRET_PATTERNS = [
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{16,}")),
    ("gemini_api_key", re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
]


def detect_high_confidence_secrets(content: str) -> list[str]:
    """Return list of credential pattern names found in `content` (zero false positives on code vars)."""
    text = content or ""
    found: list[str] = []
    for label, pat in HIGH_CONFIDENCE_SECRET_PATTERNS:
        if pat.search(text):
            found.append(label)
    return found

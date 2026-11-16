"""Safe .env secret storage — atomic 0600 writes, comment preservation, and masked status."""
from __future__ import annotations

import os
import re
from pathlib import Path

MANAGED_ENV_KEYS = (
    # Cloud LLM Providers
    "GEMINI_API_KEY",
    "AI_MODEL_GEMINI",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "AI_MODEL_OPENAI",
    "CODEX_API_KEY",
    "CODEX_BASE_URL",
    "AI_MODEL_CODEX",
    "ANTHROPIC_API_KEY",
    "AI_MODEL_ANTHROPIC",
    "AI_PROVIDER_ORDER",
    "ALLOW_PROVIDER_FALLBACK",
    # Telegram Live Bot, Callbacks & sendDocument
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_ID",
)

SECRET_ENV_KEYS = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
}

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def mask_secret(value: str) -> str:
    """Return a safe masked hint without ever exposing the raw credential."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "••••••••"
    return f"{v[:4]}••••{v[-3:]}"


def read_env_dict(env_path: str | Path = ".env") -> dict[str, str]:
    """Read key-value pairs from .env merged with current process environment."""
    file_vals: dict[str, str] = {}
    p = Path(env_path)
    if p.is_file():
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = _KEY_RE.match(line)
            if not m:
                continue
            k = m.group(1)
            v = m.group(2).strip().strip('"').strip("'")
            file_vals[k] = v

    merged: dict[str, str] = {}
    for k in MANAGED_ENV_KEYS:
        merged[k] = file_vals.get(k, os.environ.get(k, ""))
    return merged


def masked_env_status(env_path: str | Path = ".env") -> dict[str, dict]:
    """Return safe UI metadata for each managed key (never includes raw secrets)."""
    vals = read_env_dict(env_path)
    out: dict[str, dict] = {}
    for k in MANAGED_ENV_KEYS:
        raw = (vals.get(k) or "").strip()
        if k in SECRET_ENV_KEYS:
            out[k] = {
                "configured": bool(raw),
                "masked": mask_secret(raw) if raw else "",
                "is_secret": True,
            }
        else:
            out[k] = {
                "configured": bool(raw),
                "value": raw,
                "is_secret": False,
            }
    return out


def _sanitize_env_value(val: str) -> str:
    # Strip CR/LF/NUL so injected newlines can never smuggle extra .env keys
    clean = (val or "").replace("\r", "").replace("\n", "").replace("\x00", "").strip()
    if any(ch in clean for ch in (" ", "#", '"', "'")):
        escaped = clean.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return clean


def update_env_file(updates: dict[str, str], env_path: str | Path = ".env",
                    *, clear_keys: list[str] | None = None) -> list[str]:
    """Atomically update allowed keys in `.env` (`0600`) and sync `os.environ`.

    Blank strings for secret keys are ignored so existing keys are preserved unless
    explicitly listed in `clear_keys`.
    """
    p = Path(env_path)
    clear_set = {k for k in (clear_keys or []) if k in MANAGED_ENV_KEYS}

    effective: dict[str, str] = {}
    for k, v in (updates or {}).items():
        if k not in MANAGED_ENV_KEYS:
            continue
        val = (str(v) if v is not None else "").strip()
        if k in SECRET_ENV_KEYS and not val and k not in clear_set:
            continue
        effective[k] = "" if k in clear_set else val

    for k in clear_set:
        effective[k] = ""

    if not effective:
        return []

    lines: list[str] = []
    if p.is_file():
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    elif Path(".env.example").is_file():
        lines = Path(".env.example").read_text(encoding="utf-8", errors="ignore").splitlines()

    seen: set[str] = set()
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        m = _KEY_RE.match(stripped) if (stripped and not stripped.startswith("#")) else None
        if m and m.group(1) in effective:
            key = m.group(1)
            seen.add(key)
            new_lines.append(f"{key}={_sanitize_env_value(effective[key])}")
        else:
            new_lines.append(raw)

    for key, val in effective.items():
        if key not in seen:
            new_lines.append(f"{key}={_sanitize_env_value(val)}")

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass

    for key, val in effective.items():
        os.environ[key] = val

    return sorted(effective.keys())

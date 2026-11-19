#!/usr/bin/env python3
"""Fail-closed preflight for the manual Live Integration workflow.

Prints only masked credential status. Never writes secret values to logs or artifacts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.web.env_store import mask_secret  # noqa: E402

OUT = Path("live-evidence")
REPORT = OUT / "preflight.md"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    uid = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
    run_tg = os.getenv("RUN_TELEGRAM", "true").strip().lower() in {"1", "true", "yes"}
    confirmed = os.getenv("FREE_TIER_CONFIRMED", "").strip().lower() == "true"

    checks.append(("Gemini API key", bool(key), mask_secret(key)))
    checks.append(("Gemini model", bool(model), model))
    checks.append(("Owner free/no-charge confirmation", confirmed, "workflow input"))
    if run_tg:
        checks.append(("Telegram bot token", bool(token), mask_secret(token)))
        checks.append(("Telegram allowed user ID", uid.isdigit(), mask_secret(uid)))

    lines = ["# Live integration preflight", "",
             "Only masked status is recorded. No credential values appear in this report.", ""]
    ok = True
    for name, passed, hint in checks:
        ok = ok and passed
        lines.append(f"- {'PASS' if passed else 'FAIL'} {name}: {hint or '(missing)'}")
        print(f"{'PASS' if passed else 'FAIL'} {name}: {hint or '(missing)'}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not ok:
        print("Preflight failed: missing required secret or owner confirmation. No live call made.",
              file=sys.stderr)
        return 2
    print(f"Preflight OK. Telegram checks: {'enabled' if run_tg else 'disabled'}.")
    print(json.dumps({"telegram_enabled": run_tg, "provider": "gemini", "model": model}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

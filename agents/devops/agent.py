"""Agent 13 — DevOps: Termux run script, service helper, deployment notes."""
from __future__ import annotations
import stat
from pathlib import Path

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a DevOps engineer for Android Termux (no root, no Docker, 3 GB RAM).
Reply with a POSIX shell script that installs and runs the app, with safety checks."""

RUN_SH = """#!/bin/sh
# Start the generated app (Termux-safe, no root).
set -eu
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
echo "starting on port $PORT"
exec python3 backend/app.py
"""

DEV_DEPLOY = """# Deployment (Android / Termux)

The generated app is a single Python process with a static frontend and SQLite.
No Docker, Redis or PostgreSQL is required.

## Run
```sh
sh scripts/run.sh            # serves http://<phone-ip>:8000
PORT=8080 sh scripts/run.sh  # custom port
```

## Persistence
- Database file: `app.db` (WAL) — back it up with `cp app.db app.db.bak`.
- Logs go to stdout; redirect with `sh scripts/run.sh > app.log 2>&1 &`.

## Keeping it alive
```sh
termux-wake-lock
```
Android may kill background processes; Termux:Boot plus `termux-wake-lock`
reduces that. The agent pipeline itself is on-demand and exits when idle.

## Health check
```sh
python3 - <<'PY'
import urllib.request, json
print(json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health")))
PY
```
"""


class Agent(BaseAgent):
    agent_id = "devops"
    name = "DevOps & Deployment Agent"
    description = "Creates run scripts and deployment guidance for Termux."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        self._write("scripts/run.sh", RUN_SH)
        self._write("docs/deployment.md", DEV_DEPLOY)
        chmod_ok = self._chmod_exec("scripts/run.sh")
        self._write("docs/env.md",
                    "# Environment\n\n| Variable | Purpose | Default |\n|---|---|---|\n"
                    "| `PORT` | HTTP port | `8000` |\n| `APP_DB` | SQLite file | `app.db` |\n\n"
                    "Never commit real secrets; this app needs none.\n")
        return AgentResult(ok=True,
                           summary=f"Deployment artifacts written (run.sh executable={chmod_ok})",
                           files=["scripts/run.sh", "docs/deployment.md", "docs/env.md"],
                           mode="deterministic")

    def _chmod_exec(self, rel: str) -> bool:
        try:
            p = Path(self.workspace) / rel
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return True
        except Exception:
            return False

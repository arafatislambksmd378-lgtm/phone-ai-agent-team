#!/usr/bin/env bash
# Health & readiness check for Phone AI Agent Team
set -euo pipefail

cd "$(dirname "$0")/.."
PID_FILE="./data/phone_agent.pid"

PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Daemon status  : RUNNING (PID $PID)"
  else
    echo "Daemon status  : STOPPED (stale PID file)"
  fi
else
  echo "Daemon status  : STOPPED (on-demand / CLI mode)"
fi

"$PY_BIN" main.py --selfcheck
echo ""
"$PY_BIN" main.py --health

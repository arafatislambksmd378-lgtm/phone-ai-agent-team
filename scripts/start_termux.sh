#!/usr/bin/env bash
# Start Phone AI Agent Team on Termux (foreground by default, or --daemon for background).
set -euo pipefail

cd "$(dirname "$0")/.."
PID_FILE="./data/phone_agent.pid"
LOG_OUT="./data/logs/daemon_stdout.log"

mkdir -p data/logs projects

PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi

# Prevent duplicate instances if already running
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Phone AI Agent Team is already running (PID $OLD_PID)."
    exit 0
  fi
  rm -f "$PID_FILE"
fi

# Acquire Termux wake-lock so Android does not suspend active builds mid-task
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

if [ "${1:-}" = "--daemon" ] || [ "${1:-}" = "-d" ]; then
  nohup "$PY_BIN" main.py >>"$LOG_OUT" 2>&1 &
  NEW_PID=$!
  echo "$NEW_PID" > "$PID_FILE"
  echo "Started Phone AI Agent Team in background (PID $NEW_PID). Logs: $LOG_OUT"
  exit 0
fi

exec "$PY_BIN" main.py "$@"

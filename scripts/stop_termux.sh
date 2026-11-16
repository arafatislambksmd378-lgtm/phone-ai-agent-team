#!/usr/bin/env bash
# Gracefully stop Phone AI Agent Team and release Termux wake-lock.
set -euo pipefail

cd "$(dirname "$0")/.."
PID_FILE="./data/phone_agent.pid"

release_wakelock() {
  if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock || true
  fi
}

if [ ! -f "$PID_FILE" ]; then
  echo "No PID file ($PID_FILE) found — service does not appear to be running."
  release_wakelock
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  echo "Stale PID file ($PID) removed."
  rm -f "$PID_FILE"
  release_wakelock
  exit 0
fi

echo "Sending SIGTERM to Phone AI Agent Team (PID $PID)..."
kill -TERM "$PID" 2>/dev/null || true

for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    release_wakelock
    echo "Stopped cleanly."
    exit 0
  fi
  sleep 0.5
done

echo "Process did not exit within 10s; sending SIGKILL to PID $PID..."
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
release_wakelock
echo "Force-stopped."

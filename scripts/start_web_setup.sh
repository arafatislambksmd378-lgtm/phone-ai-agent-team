#!/usr/bin/env bash
# Launch the API Key & Live Verification Web Portal on Termux or Linux (default 0.0.0.0:8080)
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${WEB_SETUP_PORT:-8080}"

PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi

echo "Starting API Key & Live Verification Portal on http://0.0.0.0:${PORT} ..."
exec "$PY_BIN" main.py --web --host 0.0.0.0 --port "$PORT"

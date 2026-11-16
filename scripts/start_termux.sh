#!/bin/sh
# Keep the bot alive in Termux (Android may kill background processes).
# Usage: sh scripts/start_termux.sh
set -eu
cd "$(dirname "$0")/.."

# Prevent Android from suspending the process while it polls Telegram.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || \
    echo "tip: install Termux:API for 'termux-wake-lock' (optional)"

if [ ! -x .venv/bin/python ]; then
    echo "ERROR: .venv missing — run: sh scripts/install_termux.sh" >&2
    exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate

mkdir -p logs data projects backups
exec python3 main.py

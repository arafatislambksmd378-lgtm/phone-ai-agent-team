#!/usr/bin/env bash
# Phone AI Agent Team — Safe, repeatable installer for Android 14 Termux (3 GB RAM)
# Also works cleanly on standard Linux development hosts.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "== Phone AI Agent Team — Installer =="

# 1. Install minimal system packages when running inside Termux
if command -v pkg >/dev/null 2>&1; then
  echo "[1/5] Termux detected — updating packages (python, git)..."
  pkg update -y
  pkg install -y python git
else
  echo "[1/5] Non-Termux POSIX host detected — checking python3 and git..."
  command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required."; exit 1; }
  command -v git >/dev/null 2>&1 || { echo "ERROR: git is required."; exit 1; }
fi

# 2. Optional lightweight virtualenv (no heavy compiled dependencies required)
PY_BIN="python3"
if [ ! -d ".venv" ] && python3 -m venv --help >/dev/null 2>&1; then
  echo "[2/5] Creating lightweight Python virtual environment (.venv)..."
  python3 -m venv .venv || true
fi
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi
echo "      Using Python: $($PY_BIN --version)"

# 3. Runtime directories & secure .env initialization
echo "[3/5] Preparing workspace and data directories..."
mkdir -p data/logs projects
chmod 700 data projects 2>/dev/null || true

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "      Created .env from .env.example (chmod 600)."
else
  chmod 600 .env 2>/dev/null || true
  echo "      Existing .env preserved (permissions set to 600)."
fi

# 4. Ensure local Git identity exists for automatic project checkpoints
echo "[4/5] Verifying Git checkpoint configuration..."
if ! git config --global user.email >/dev/null 2>&1; then
  git config --global user.email "agent@phone-local"
  git config --global user.name "Phone AI Agent Team"
  echo "      Set fallback global git identity for local checkpoints."
fi

# 5. Executable scripts & self-check
echo "[5/5] Running system self-check..."
chmod +x scripts/*.sh 2>/dev/null || true
"$PY_BIN" main.py --selfcheck

echo ""
echo "== Installation complete =="
echo "Next steps:"
echo "  1. Edit .env (set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID, and GEMINI_API_KEY / OPENAI_API_KEY)"
echo "  2. Quick local smoke run : $PY_BIN main.py --demo --quick"
echo "  3. Start Telegram bot    : ./scripts/start_termux.sh          (foreground)"
echo "                             ./scripts/start_termux.sh --daemon (background)"
echo "  4. Stop Telegram bot     : ./scripts/stop_termux.sh"

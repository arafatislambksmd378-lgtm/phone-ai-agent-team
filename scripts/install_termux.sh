#!/bin/sh
# Phone AI Agent Team — Termux installer (no root, no VPS)
set -e
echo "=== Phone AI Agent Team installer ==="
pkg update -y
pkg install -y python git
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp -n .env.example .env || true
echo "Done. Edit .env (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID, GEMINI_API_KEY...)"
echo "Then run:  . .venv/bin/activate && python3 main.py"
echo "Demo:      . .venv/bin/activate && python3 main.py --demo"

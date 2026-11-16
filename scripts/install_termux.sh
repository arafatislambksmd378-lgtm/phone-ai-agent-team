#!/bin/sh
# Phone AI Agent Team — Android/Termux installer (no root required).
set -eu

cd "$(dirname "$0")/.."

echo "== 1/6 checking Termux environment =="
if ! command -v pkg >/dev/null 2>&1; then
    echo "ERROR: 'pkg' not found. This script is for Termux on Android." >&2
    echo "On a desktop, use: python3 -m venv .venv && . .venv/bin/activate && pip install pytest" >&2
    exit 1
fi

echo "== 2/6 installing system packages (python, git) =="
pkg install -y python git

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: python3 still missing after pkg install." >&2
    exit 1
fi
echo "python: $(python3 --version 2>&1)"

echo "== 3/6 creating virtual environment (.venv) =="
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv || {
        echo "venv creation failed; installing python-venv and retrying" >&2
        pkg install -y python-venv || true
        python3 -m venv .venv
    }
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "== 4/6 installing Python dependencies =="
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "== 5/6 preparing configuration =="
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "created .env (mode 600) — fill in your tokens next"
else
    echo ".env already exists — left untouched"
fi
mkdir -p data projects logs backups

echo "== 6/6 verifying the installation =="
python3 main.py --selfcheck || {
    echo "self check failed — see messages above" >&2
    exit 1
}

echo
echo "=============================================================="
echo " Installation complete."
echo
echo " Next steps:"
echo "   1. edit .env   -> TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID"
echo "                     and at least one AI key (GEMINI_API_KEY)"
echo "   2. activate    -> . .venv/bin/activate"
echo "   3. dry run     -> python3 main.py --demo"
echo "   4. go live     -> python3 main.py"
echo "=============================================================="

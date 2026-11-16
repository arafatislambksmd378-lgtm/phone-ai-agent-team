# Termux Setup (Android 14, 3GB RAM, no root)

1. Install Termux from F-Droid (not Play Store).
2. Open Termux:
```sh
pkg update -y && pkg install -y python git
git clone https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team.git
cd phone-ai-agent-team
sh scripts/install_termux.sh
```
3. Edit `.env` — set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and at least one AI key (`GEMINI_API_KEY` recommended).
4. Start: `. .venv/bin/activate && python3 main.py`
5. Test without Telegram: `python3 main.py --demo`

Keep Termux in foreground or use `termux-wake-lock`. The Telegram listener is lightweight polling; AI workers run on-demand (1 at a time) and stop after each project.

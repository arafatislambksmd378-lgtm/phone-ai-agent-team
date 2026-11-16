# Telegram Setup

1. Talk to @BotFather → /newbot → copy token → `TELEGRAM_BOT_TOKEN`.
2. Talk to @userinfobot to get your numeric ID → `TELEGRAM_ALLOWED_USER_ID`.
3. Start bot: `python3 main.py`
4. Commands: `/start /help /new /status /agents /pause /resume /stop /approve /reject /logs /report /projects /recover /settings`
5. Send TXT/MD/PDF/DOCX/JSON/ZIP files — validated (type/size), ZIPs extracted safely.
6. Only the allowed user ID can run admin commands. Secrets are redacted from messages.

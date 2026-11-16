# Telegram setup

## 1. Create the bot

1. Open Telegram, search **@BotFather**, start a chat.
2. Send `/newbot`, choose a display name and a username ending in `bot`.
3. BotFather replies with a token like `8123456789:AAH...`. That is
   `TELEGRAM_BOT_TOKEN`.

Treat the token like a password. If it leaks, `/revoke` in BotFather and update `.env`.

## 2. Find your numeric user id

Message **@userinfobot** — it replies with `Id: 123456789`. That number is
`TELEGRAM_ALLOWED_USER_ID`.

**The bot accepts commands from this id only.** Everyone else receives
"⛔ Not authorized." and is logged. If the value is empty, nobody is authorized.

## 3. Start

```sh
. .venv/bin/activate
python3 main.py
```

You should see `listening for Telegram updates; workers start on demand`.

## 4. Commands

| Command | What it does |
|---|---|
| `/start`, `/help` | welcome + command list |
| `/new <name>` | create a project and plan the 16-agent pipeline |
| `/status [id]` | lifecycle state + task list |
| `/agents` | list the 16 agents |
| `/pause`, `/resume` | pause/resume the worker pool |
| `/stop <id>` | cancel a project |
| `/approve <id>`, `/reject <id>`, `/pending` | approval workflow |
| `/logs [id]` | last 20 execution log lines |
| `/report [id]` | completion report with AI usage |
| `/projects` | all projects |
| `/recover <id>` | requeue interrupted / failed tasks |
| `/settings`, `/health` | runtime config (never secrets) |

## 5. Sending blueprints

After `/new myapp`, send **text**, or attach one of:
`TXT, MD, PDF, DOCX, JSON, ZIP, CSV, SQL, YAML, TOML, PY, JS, HTML`.

Validation happens in three stages:

1. **Metadata** — extension must be allowlisted, size ≤ `MAX_UPLOAD_MB` (20 MB).
2. **Download** — streamed to a temp file, hard byte cap, partial file deleted on abort.
3. **Content** — magic bytes must match the extension (`%PDF`, `PK\x03\x04`);
   renamed binaries are rejected.

ZIP archives are additionally checked for path traversal, absolute paths, member
count, uncompressed size and compression ratio before extraction.

## 6. What the bot never does

- It never prints API keys or tokens (`/settings` shows provider *status*, not secrets).
- It never runs a command found inside a document.
- It never pushes to git, deletes files or deploys without an approval record.

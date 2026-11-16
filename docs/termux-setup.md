# Installing on Android with Termux (beginner guide)

Target: **Android 14, 3 GB RAM, 64 GB storage, no root, no VPS.**
Everything below happens on the phone.

## 1. Install Termux

Install Termux **from F-Droid** (https://f-droid.org/packages/com.termux/), not from
Google Play — the Play version is obsolete and breaks package installs.

Optional but recommended: also install **Termux:API** and **Termux:Boot** from F-Droid
(same source as Termux, otherwise the add-ons do not work).

## 2. First launch

Open Termux and paste this block (long-press → Paste, then Enter):

```sh
pkg update -y && pkg upgrade -y
pkg install -y python git
```

Check the Python version — you need **3.10 or newer**:

```sh
python3 --version
```

## 3. Get the code

```sh
cd ~
git clone https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team.git
cd phone-ai-agent-team
```

## 4. Run the installer

```sh
sh scripts/install_termux.sh
```

The installer:

1. checks you are really in Termux,
2. installs `python` + `git`,
3. creates a virtual environment in `.venv`,
4. installs the single dev dependency (`pytest`),
5. creates `.env` with permissions `600`,
6. runs `python3 main.py --selfcheck` and prints the result.

If the self check prints `self check passed.` you are ready.

## 5. Activate the environment (every new Termux session)

```sh
cd ~/phone-ai-agent-team
. .venv/bin/activate
```

## 6. Try it without any keys

```sh
python3 main.py --demo
```

This runs the complete 16-agent pipeline in **mock mode** (no AI calls). It takes a
few seconds and prints a report. The generated project lands in `projects/`.

## 7. Configure Telegram + AI

```sh
nano .env
```

Fill in:

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram → **@BotFather** → `/newbot` |
| `TELEGRAM_ALLOWED_USER_ID` | Telegram → **@userinfobot** → "Id" |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free tier) |

Save with `Ctrl+O`, `Enter`, exit with `Ctrl+X`.

Then confirm the config was read:

```sh
python3 main.py --health
```

You should see `AI mode : live` and `gemini CONFIGURED`.

## 8. Start the bot

```sh
sh scripts/start_termux.sh
```

Open your bot in Telegram and send `/start`.

## 9. Keep it running

Android aggressively kills background apps. Options, easiest first:

```sh
termux-wake-lock          # keeps Termux awake (needs Termux:API)
```

Then disable battery optimisation for Termux in Android settings:
**Settings → Apps → Termux → Battery → Unrestricted**.

With **Termux:Boot** installed, create `~/.termux/boot/start-bot.sh`:

```sh
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/phone-ai-agent-team
sh scripts/start_termux.sh >> logs/boot.log 2>&1
```

and make it executable: `chmod +x ~/.termux/boot/start-bot.sh`.

## 10. Stop it

Press `Ctrl+C` in Termux. Workers stop immediately; the database is crash-safe (WAL),
so an unclean stop loses nothing.

## 11. After a crash or reboot

```sh
cd ~/phone-ai-agent-team && . .venv/bin/activate
python3 main.py --recover     # requeue interrupted tasks
python3 main.py --status      # see what happened
```

Or from Telegram: `/recover <project_id>`.

## Storage and memory reality check

| Item | Measured (Linux, same code) | Notes |
|---|---|---|
| Idle process RSS | ~23 MB | Telegram listener + SQLite only |
| Full 16-agent run RSS | ~35 MB | no AI calls in mock mode |
| Disk per project | ~250 KB | code + docs + git history |
| Database | ~120 KB | grows ~1 KB per task |

These measurements were taken on Linux; **the actual numbers on your phone still need to
be measured** (see `docs/operations.md` → "Benchmarking on the device").

## Troubleshooting

See `docs/troubleshooting.md`.

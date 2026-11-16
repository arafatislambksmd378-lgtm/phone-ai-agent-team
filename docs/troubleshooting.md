# Troubleshooting

## Installation

| Symptom | Cause | Fix |
|---|---|---|
| `pkg: command not found` | Not running in Termux (e.g. a desktop shell) | On desktop use `python3 -m venv .venv && pip install pytest`; the installer refuses to run. |
| `python3: command not found` | Packages not installed | `pkg install -y python git` |
| `ensurepip is not available` | Termux venv package missing | `pkg install -y python-venv`, then rerun the installer |
| `git clone` asks for a password | Repository is private | Use the HTTPS URL with a personal access token, or make the repo public |
| `Permission denied` running a script | Missing exec bit | `sh scripts/install_termux.sh` (invoke through `sh`) |
| `pip` tries to compile wheels for hours | Old pip | `pip install --upgrade pip` (the installer does this) |

## Configuration

| Symptom | Cause | Fix |
|---|---|---|
| `TELEGRAM_BOT_TOKEN is not set` | `.env` missing/not filled | `cp .env.example .env`, add the token |
| `every command would be rejected` | `TELEGRAM_ALLOWED_USER_ID` empty | Get your id from **@userinfobot** |
| Bot silent after start | Another instance is polling the same token | Stop the other instance (`pkill -f main.py`) |
| `AI mode: mock` | No provider key | Add `GEMINI_API_KEY` and rerun `--health` |
| `AI mode: failed` on every task | Key invalid / no network / quota | Check `/health` in Telegram and the log lines `provider … failed` |

**Mock vs live:** `mock` means *no AI was used* — the agents produced their deterministic
templates. If you expect real AI, `/health` must show `AI mode: live` and the provider as
`CONFIGURED`.

## Runtime

| Symptom | Cause | Fix |
|---|---|---|
| `database is locked` | Two processes on one DB | Run a single `main.py` per `DB_PATH` |
| Tasks stuck in `EXECUTING` | Process was killed | `python3 main.py --recover` or `/recover <id>` |
| Project state `FAILED` | One or more tasks exhausted retries | `/status <id>`, fix the cause, `/recover <id>` |
| Project state `WAITING_FOR_APPROVAL` | An approval is pending | `/pending`, then `/approve <id>` |
| Workers never start | No `QUEUED` task, or pool paused | `/resume`, then `/status` |
| Memory grows during long runs | Many projects kept open | Restart the bot; SQLite keeps state |
| Android kills the bot overnight | Battery optimisation | `termux-wake-lock` + unrestricted battery |

## Generated project

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` | Port 8000 busy | `PORT=8080 sh scripts/run.sh` |
| Frontend blank | Backend not running | Start `scripts/run.sh`, reload |
| `pytest: command not found` in a generated project | Tests run inside the agent venv | Activate `.venv` of the agent, not the generated app |
| `AttributeError: module 'app_under_test'` in generated tests | Old QA template | Re-run the project (the template was fixed) |

## Security checks

| Symptom | Meaning |
|---|---|
| `⛔ Not authorized.` | Your Telegram id is not in `TELEGRAM_ALLOWED_USER_ID` |
| `Command blocked by policy` | The command is not on the allowlist — by design |
| `Requires human approval` | The command needs `/approve <id>` first |
| `File is not a valid PDF` | Magic bytes do not match the extension |
| `Path traversal blocked` | The ZIP contained `..`, an absolute path or a sibling-prefix escape |

## Getting help

```sh
python3 main.py --selfcheck   # dependency, directory and DB verification
python3 main.py --health      # provider + database status
tail -n 50 logs/agent.log     # recent activity (secrets already redacted)
```

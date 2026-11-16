# Phone AI Agent Team

A 16-agent AI software-development pipeline that runs **on your Android phone** in
Termux — no VPS, no Docker, no PostgreSQL, no Redis, no local LLM.

You send a blueprint to a Telegram bot. The system plans a 16-step pipeline, runs each
specialist agent on demand (one at a time by default), generates a working project with
tests and documentation, and reports back. AI workers stop when the project finishes; the
lightweight Telegram listener stays up.

## Status (honest)

| Area | State |
|---|---|
| 16 agents, registry, pipeline | Implemented, 136 automated tests passing |
| SQLite queue, recovery, approvals, lifecycle | Implemented, tested |
| AI router + Gemini / OpenAI-compatible / Anthropic | Implemented; request formats unit-tested |
| **Live AI calls** | **Not verified** — needs your API key (see `/health`) |
| Telegram bot | Implemented; **not verified against the live API** — needs your bot token |
| Termux install | Scripted; **not yet run on a physical Android 14 device** |

Mock mode is clearly labelled: with no API key the agents produce deterministic
templates and every result is marked `mock`. Mock is **not** AI-generated output.

See `docs/audit-2026-11.md` for the independent audit and the list of fixed defects.

## Quick start (Termux)

```sh
pkg update -y && pkg install -y python git
git clone https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team.git
cd phone-ai-agent-team
sh scripts/install_termux.sh
nano .env                 # TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID, GEMINI_API_KEY
. .venv/bin/activate
python3 main.py --demo    # offline dry run, no keys needed
python3 main.py           # go live
```

Full beginner walkthrough: **`docs/termux-setup.md`**.

## The 16 agents

| # | Agent | Responsibility |
|---|---|---|
| 01 | Chief Orchestrator | Coordinates the pipeline, writes the orchestration report |
| 02 | Requirement Analyst | Structured spec from text/ZIP/PDF/MD |
| 03 | Software Architect | Architecture doc + runnable backend skeleton |
| 04 | UI/UX Designer | Screens, flows, design tokens |
| 05 | Frontend Developer | Responsive SPA wired to the API |
| 06 | Backend Developer | API implementation and hardening |
| 07 | Database Engineer | SQLite schema, migration, live verification |
| 08 | API Integrator | Retrying HTTP adapter for external APIs |
| 09 | Security Engineer | Static audit of the generated code |
| 10 | QA & Testing | Writes **and executes** the test suite |
| 11 | Debugger | Reads real failures, applies bounded repairs |
| 12 | Code Reviewer | Length, duplication, silent-failure checks |
| 13 | DevOps | `run.sh`, deployment notes, executable bits |
| 14 | Documentation | README + troubleshooting from real state |
| 15 | Release Manager | Readiness gate, version bump, release notes |
| 16 | Workspace Executor | Allowlisted, approved commands only |

## Telegram commands

`/start /help /new /status /agents /pause /resume /stop /approve /reject /pending
/logs /report /projects /recover /settings /health`

Send `TXT MD PDF DOCX JSON ZIP CSV SQL YAML TOML` as a blueprint (20 MB limit,
magic-byte checked, ZIP traversal-protected).

## CLI

```sh
python3 main.py               # bot + on-demand workers
python3 main.py --demo        # full pipeline, offline
python3 main.py --demo --quick
python3 main.py --selfcheck    # verify the installation
python3 main.py --health      # provider + database status
python3 main.py --status      # project list
python3 main.py --recover     # requeue interrupted tasks
```

## Documentation

| File | Contents |
|---|---|
| `docs/termux-setup.md` | Android installation, step by step |
| `docs/telegram-setup.md` | Bot token, user id, command reference |
| `docs/architecture.md` | Components, lifecycle, data model, security boundaries |
| `docs/operations.md` | Run, backup, restore, recovery, benchmarking |
| `docs/troubleshooting.md` | Symptom → cause → fix |
| `docs/security.md` | Threat model and controls |
| `docs/audit-2026-11.md` | Independent audit: 20 findings, all fixed |
| `tests/README.md` | Requirement → test mapping |

## Requirements

- Android 14 (Termux), ~3 GB RAM, ~1.5 MB disk + ~250 KB per project
- Python 3.10+
- One AI provider key (Gemini free tier is enough)
- A Telegram bot token

## Tests

```sh
python3 -m pytest -q     # 136 passed
```

## License

See `LICENSE` (MIT).

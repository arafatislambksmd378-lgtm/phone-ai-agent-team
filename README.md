# Phone AI Agent Team

On-demand 16-agent AI software development automation for Android Termux (3GB RAM, no VPS, no root).

Send a blueprint to Telegram → the system plans, assigns 16 specialized agents, writes code, tests, reviews, documents, and reports back. Agents are **logical workers** invoked on demand (1 at a time by default) — never 16 background processes. The Telegram listener stays up; AI workers stop after each project.

## Quick start (Termux)

```sh
pkg update -y && pkg install -y python git
git clone https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team.git
cd phone-ai-agent-team
sh scripts/install_termux.sh
# edit .env: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID, GEMINI_API_KEY (or OPENAI/ANTHROPIC)
. .venv/bin/activate && python3 main.py
# no Telegram yet?  python3 main.py --demo
```

## 16 Agents

| # | Agent | Role |
|---|-------|------|
| 01 | Chief Orchestrator | Coordination, scheduling, recovery, reporting |
| 02 | Requirement Analyst | Extracts FR/NFR from docs/ZIP/text |
| 03 | Software Architect | Architecture, modules, interfaces |
| 04 | UI/UX Designer | Screens, journeys, design system |
| 05 | Frontend Developer | Responsive UI + API wiring |
| 06 | Backend Developer | Services, APIs, auth, logic |
| 07 | Database Engineer | Schemas, migrations, queries |
| 08 | API Integrator | External APIs, retries, adapters |
| 09 | Security Engineer | Vulns, secrets, input validation |
| 10 | QA & Testing | Test plans, execution, reports |
| 11 | Debugger | Root cause, controlled fixes |
| 12 | Code Reviewer | Quality, dedup, performance |
| 13 | DevOps | Install scripts, deployment |
| 14 | Documentation | README, guides, API docs |
| 15 | Release Manager | Readiness, versioning, notes |
| 16 | Workspace Executor | Safe files, approved commands, git |

## Telegram commands

`/start /help /new /status /agents /pause /resume /stop /approve /reject /logs /report /projects /recover /settings` — plus send TXT/MD/PDF/DOCX/JSON/ZIP blueprints.

## AI providers

Gemini, OpenAI-compatible, Anthropic via `integrations/ai_providers/` with fallback + usage tracking. No keys → deterministic mock mode (offline-safe).

## Docs

- `docs/termux-setup.md` — phone install
- `docs/telegram-setup.md` — bot setup
- `docs/architecture.md` — system design
- `docs/operations.md` — run/backup/recover

## Tests

```sh
python3 -m pytest -q
```

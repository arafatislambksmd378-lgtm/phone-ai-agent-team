# Phone AI Agent Team

**Phone-Based On-Demand 16-Agent AI Software Development System for Android 14 (Github, 3 GB RAM)**

`phone-ai-agent-team` turns a single Android 14 smartphone running Termux into a Telegram-controlled, SQLite-backed, multi-agent software engineering platform. Instead of keeping 16 heavyweight daemons resident in memory, the runtime stays idle at **~26 MB RSS** with a zero-dependency Python 3.11+ standard-library Telegram listener and activates **16 logical specialist agent roles on demand** through a dependency-aware Task DAG, verified agent-to-agent handoffs, and an execution-layer human approval gate.

---

## Key Capabilities

- **16 Logical Specialist AI Agents** — Chief Orchestrator, Requirement Analyst, Software Architect, UI/UX Designer, Frontend Developer, Backend Developer, Database Engineer, API & Integration Agent, Security Engineer, QA & Testing Agent, Debugging & Code Repair Agent, Code Review & Optimization Agent, DevOps & Deployment Agent, Technical Documentation Agent, Release Manager, and Workspace & Tool Execution Agent.
- **Blueprint-Driven Dynamic Agent Selection** — Classifies blueprints into `web_app`, `rest_api`, `backend_service`, `cli_tool`, `python_library`, `database_app`, `android_app`, or `desktop_app`, builds a cycle-checked multi-parent Task DAG, and automatically excludes irrelevant agents (for example, skipping UI/UX and Frontend for a REST API or CLI tool).
- **Multi-Provider AI Model Router** — Supports **Google Gemini**, **OpenAI**, **OpenAI Codex / compatible endpoints**, **Anthropic Claude**, and pluggable custom adapters (`router.register_provider(...)`) with retries, HTTP 429 `Retry-After` backoff, automatic fallback, token/cost telemetry, and deterministic offline fallback when no API keys are configured.
- **Verified Agent-to-Agent Handoffs** — Persists SHA-256 artifact digests and structured outputs in SQLite (`task_handoffs`) so downstream agents receive real upstream specifications, schemas, test failures, and source files.
- **Execution-Layer Security & Telegram Inline Approvals** — Non-bypassable `ApprovalManager` gates production deploys, destructive file/DB operations, public GitHub pushes/releases, external account changes, paid AI spend thresholds, and secret operations via inline **✅ Approve / ❌ Reject** Telegram buttons with TTL expiration, duplicate callback protection, and full audit logging.
- **Hardened Document & Archive Ingestion** — Extracts requirements from `.txt`, `.md`, `.json`, `.csv`, `.pdf`, `.docx`, `.zip`, and source files using Python stdlib only, with traversal, symlink, compression-ratio, executable-blocking, and size caps.
- **Android 14 / 3 GB RAM Resource Governance & Crash Recovery** — Enforces 1 active worker by default (optional 2nd worker gated by live RAM/CPU/storage checks), tracks child subprocesses, cleans temporary artifacts, creates automatic local Git checkpoints after every completed task, and recovers interrupted tasks after Termux restarts.

---

## Quick Start (Android Termux or Linux)

```bash
# 1. Install and run self-check
./scripts/install_termux.sh

# 2. Configure credentials (chmod 600 .env)
nano .env

# 3. Run an instant local build without Telegram (offline or live AI)
python3 main.py --demo --quick
python3 main.py --demo --blueprint "Build a Python CLI tool to Bookmark URLs with SQLite search"

# 4. Open the Web API Key & Live Verification Portal (paste LLM & Telegram keys in browser)
./scripts/start_web_setup.sh

# 5. Start the Telegram listener directly (foreground or background daemon)
./scripts/start_termux.sh
./scripts/start_termux.sh --daemon

# 6. Check health or stop cleanly
./scripts/healthcheck.sh
./scripts/stop_termux.sh
```

---

## Repository Layout

```text
phone-ai-agent-team/
├── main.py                       # CLI & Telegram daemon entry point
├── agents/                       # All 16 logical specialist agents + contracts + helpers
├── orchestrator/                 # Dynamic DAG planner, handoffs, scheduler, lifecycle, engine
├── integrations/
│   ├── ai_providers/             # Gemini, OpenAI/Codex, Anthropic & extensible ModelRouter
│   ├── telegram/                 # Long-polling bot, inline approval callbacks, ZIP delivery
│   └── github/                   # Ephemeral-auth GitHub client, secret scan & backup verify
├── execution/                    # ProcessManager, ResourceManager, CommandRunner, FileManager,
│                                 # DocumentProcessor, Sandbox (Git checkpoints), Packager
├── memory/                       # SQLite WAL database (schema v3) & ProjectMemory
├── security/                     # ApprovalManager, allowlists, ZIP guard, SecretManager
├── config/                       # Env-driven Settings & per-agent model tier mapping
├── scripts/                      # install_termux.sh, start_termux.sh, stop_termux.sh, healthcheck.sh
├── docs/                         # Complete architecture, security, agent, testing & audit docs
└── tests/                        # Deterministic unit, integration, security, E2E & live tests
```

---

## Documentation Index

| Document | Purpose |
|---|---|
| [`docs/INSTALLATION.md`](docs/INSTALLATION.md) | Step-by-step Android 14 Termux & Linux installation guide |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Complete `.env` reference for Telegram, AI providers, GitHub & limits |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | On-demand runtime architecture, SQLite schema v3, DAG & handoffs |
| [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md) | Detailed reference for all 16 specialist agents & I/O contracts |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, approval gates, upload hardening & secret redaction |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | Crash recovery, stale lock reclamation, Git checkpoints & `/recover` |
| [`docs/TESTING.md`](docs/TESTING.md) | Running unit, security, E2E & opt-in live provider/Telegram tests |
| [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) | Honest verification matrix & operational readiness report |
| [`docs/COMPREHENSIVE_AUDIT.md`](docs/COMPREHENSIVE_AUDIT.md) | Initial codebase audit findings (`AUD-01`–`AUD-14`) |
| [`docs/REQUIREMENT_TRACEABILITY.md`](docs/REQUIREMENT_TRACEABILITY.md) | Requirement-to-module-to-test traceability matrix |

## GitHub-Centric Platform (v3)
Android app (`android/`, built by GitHub Actions — never on-device) + Telegram both use the shared JSON API: `API_AUTH_TOKEN=x python3 main.py --api --api-port 8000`. Each project binds its own GitHub repo (`POST /api/projects/{id}/repository`); agents work on `agent/<project>/<task>` branches and open PRs via `integrations/github/` — merges are approval-gated and cross-project repo access is rejected. Set `GITHUB_TOKEN` (fine-grained: Contents+Pull requests RW on selected repos); without it remote calls fail honestly, nothing is simulated. See `docs/architecture/` and `docs/implementation/`.

## Live provider & Telegram verification (manual, secure)
Real Gemini/Telegram checks run through a manually dispatched GitHub Actions workflow; credentials
are entered in GitHub Secrets, never in chat or Git. See [`docs/LIVE_INTEGRATION_SETUP.md`](docs/LIVE_INTEGRATION_SETUP.md) and `MASTER_BATCH_02_LIVE_INTEGRATION_REPORT.md`.

## Durable task control and project memory (schema v6)
The Android Tasks screen, the API and Telegram `/task <id> [cancel|retry|resume]` all act on the same persisted task row. Cancellation survives a backend restart, retries use exponential backoff (`RETRY_BACKOFF_BASE_SECONDS`, `RETRY_BACKOFF_MAX_SECONDS`), completed tasks cannot be re-run, and control requests accept an `idempotency_key`. Structured project knowledge is versioned, deduplicated, searchable and project-scoped via `GET/POST /api/projects/{id}/memory` and the Android Memory screen (`MEMORY_VERSIONS_PER_KEY` controls retention). See `docs/IMPLEMENTATION_STATUS.md` and `docs/REPOSITORY_GAP_ANALYSIS.md`.

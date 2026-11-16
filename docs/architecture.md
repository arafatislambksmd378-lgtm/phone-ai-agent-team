# Architecture

## Design constraints

- Android 14 + Termux, 3 GB RAM, no root, no VPS.
- **No** Docker, PostgreSQL, Redis, message broker or local LLM.
- One Python process; agents are **logical workers**, not 16 processes.
- Everything below the AI provider layer uses the standard library only.

## Component map

```
                 ┌──────────────────────────────────────────┐
  Telegram ─────►│ integrations/telegram/{bot,handler}.py   │  long polling
  (user)         │  auth → intent → reply (redacted)        │
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ orchestrator/engine.py                   │
                 │  create_project → ingest_upload →        │
                 │  planner → scheduler → finish/project    │
                 └───┬───────────────┬──────────────┬───────┘
                     ▼               ▼              ▼
        ┌────────────────┐  ┌──────────────┐  ┌─────────────────┐
        │ orchestrator/  │  │ orchestrator/│  │ memory/         │
        │ planner.py     │  │ scheduler.py │  │ database.py     │
        │ 16-step DAG    │  │ 1–2 threads  │  │ SQLite WAL      │
        └────────────────┘  └──────┬───────┘  └─────────────────┘
                                   ▼
                      ┌────────────────────────────┐
                      │ agents/registry.py         │
                      │  lazy import, 16 agents    │
                      └──────┬──────────────┬──────┘
                             ▼              ▼
                 ┌───────────────────┐  ┌──────────────────────┐
                 │ execution/        │  │ integrations/        │
                 │ FileManager       │  │ ai_providers/        │
                 │ CommandRunner     │  │  gemini|openai|      │
                 │ Sandbox (git)     │  │  anthropic + router  │
                 └───────────────────┘  └──────────────────────┘
```

## Lifecycle

```
IDLE → INPUT_RECEIVED → VALIDATING → PLANNING → QUEUED → EXECUTING
     → TESTING → REVIEWING → COMPLETED
```

Side states: `PAUSED`, `FAILED`, `CANCELLED`, `RECOVERING`, `WAITING_FOR_APPROVAL`.
`COMPLETED` and `CANCELLED` are terminal and refuse further transitions.

The state is persisted in `projects.lifecycle`, so a restart resumes from the real state.
The project only reaches `COMPLETED` when **every** task is final and **no** approval is
pending; failures end in `FAILED` and can be requeued with `/recover`.

## Task model

- `tasks` rows are claimed with a single guarded `UPDATE … WHERE status='QUEUED'`.
- Dependencies live in `task_dependencies`; a task is only eligible when all its
  dependencies are `COMPLETED` or `SKIPPED`.
- Planning is idempotent: `(project_id, agent_id, title)` has a unique index, so
  re-planning never duplicates work.
- Retries are bounded by `max_retries` (default 3) and counted on the row.

## Worker model

`Scheduler` runs 1 thread by default (2 max, clamped). A worker claims a task, executes
the agent **in-process**, records the result and a git checkpoint, then loops. When no
runnable task remains the pool stops itself — workers never keep a phone awake.

The Telegram listener is a separate long-polling loop in the main thread and stays up.

## AI routing

`ModelRouter` keeps one adapter per provider. For each call it walks
`AI_PROVIDER_ORDER`, skipping providers without a key. A provider failure is classified
(`AUTH_ERROR`, `RATE_LIMITED`, `TIMEOUT`, `CONNECTION_FAILED`, `SERVER_ERROR`,
`MALFORMED_RESPONSE`, `BAD_REQUEST`, `NOT_CONFIGURED`) and only retryable classes are
retried. With fallback enabled the next provider is tried.

Result modes are explicit and never conflated:

| Mode | Meaning |
|---|---|
| `live` | a real provider returned text |
| `mock` | no provider configured — deterministic local output, **not AI** |
| `failed` | providers configured, all attempts failed |

Tokens and estimated cost are recorded in `model_usage`, shown by `/report`.

## Persistence

SQLite in WAL mode with `synchronous=NORMAL` and `busy_timeout=30s`. Tables: `projects`,
`tasks`, `task_dependencies`, `agents`, `executions`, `execution_logs`, `model_usage`,
`approvals`, `project_memory`, `checkpoints`, `artifacts`, `settings`, `migrations`.
`Database.backup()` uses SQLite's online backup API, so backups are consistent while the
bot is running.

## Security boundaries

| Boundary | Control |
|---|---|
| Telegram | allow-listed numeric user id; unset ⇒ deny all |
| Uploads | extension allowlist, size cap, magic-byte check |
| ZIP | traversal, absolute path, count, size, ratio limits |
| Filesystem | every path resolved under the project workspace (`commonpath`) |
| Commands | allowlist, no shell, no `-c`/`-e` inline eval, timeouts, scrubbed env |
| Secrets | provider keys never logged; all log/message text passes through `redact()` |
| Destructive actions | approval record required, decisions are single-shot |

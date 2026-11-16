# Architecture

```
Telegram (lightweight poller, always on)
  → OrchestratorEngine → Planner (DAG) → Scheduler (1 worker default)
    → AgentRegistry → 16 logical agents (invoked on demand, never 16 processes)
      → ModelRouter → Gemini / OpenAI / Anthropic (+ mock fallback)
      → FileManager / CommandRunner (allowlist) / Sandbox (git checkpoints)
  → SQLite (projects, tasks, deps, executions, logs, usage, approvals, memory, artifacts)
```

Lifecycle: IDLE → INPUT_RECEIVED → VALIDATING → PLANNING → QUEUED → EXECUTING → TESTING → REVIEWING → COMPLETED (+ PAUSED/FAILED/CANCELLED/RECOVERING/WAITING_FOR_APPROVAL).

Resource rules: no local LLMs, no Docker/Postgres/Redis, SQLite WAL, streaming file I/O, 1 worker (2 only if validated), timeouts everywhere.

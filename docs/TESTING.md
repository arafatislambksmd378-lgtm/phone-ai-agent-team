# Testing & Verification Guide

The test suite is split into **fast, 100% deterministic offline tests** (default) and **opt-in live external integration tests** gated by environment variables so CI and offline Termux runs never hang or fail due to missing API keys.

---

## 1. Run the Full Deterministic Test Suite

```bash
# Using virtualenv pytest (recommended)
.venv/bin/pytest -v

# Or system pytest on Termux
pytest -v
```

### Test Modules Overview

| Test File | Coverage Focus |
|---|---|
| `tests/test_all_agents.py` & `tests/test_agents.py` | All 16 logical agents, `AgentContract` validation, input/output schemas, execution logging, and multi-workflow code generation |
| `tests/test_dynamic_planner.py` | Blueprint classification (`web_app`, `rest_api`, `cli_tool`, `python_library`, `database_app`, `android_app`, `desktop_app`), irrelevant-agent exclusion, Kahn cycle rejection, and dynamic replanning |
| `tests/test_handoffs_and_workflows.py` | `HandoffManager` SHA-256 artifact verification, upstream-to-downstream context propagation, failure propagation (`SKIPPED`), debugger repair loop, and end-to-end builds for REST API, CLI tool, and Python library |
| `tests/test_document_and_github.py` | TXT, MD, JSON, CSV, PDF, DOCX, ZIP extraction, corrupted/traversal/bomb rejection, `GitHubClient` pre-push secret scan, approval enforcement, and `ResourceManager` worker throttling |
| `tests/test_approvals.py` | Execution-layer `ApprovalManager` enforcement (`CommandRunner`, `FileManager`, `GitHubClient`, paid cost cap), TTL expiration, single-use consumption, and `approval_audit_logs` |
| `tests/test_telegram.py` | Auth guard, all slash commands (`/start` through `/recover`, `/build`, `/artifact`), inline approval `callback_query` buttons, duplicate/unauthorized callback rejection, and ZIP delivery |
| `tests/test_security.py` | Path traversal, symlink escapes, ZIP bombs, command allowlists, shell metacharacter blocking, and secret redaction |
| `tests/test_providers.py` & `tests/test_router.py` | Gemini, OpenAI, Codex, Anthropic adapters, custom provider registration, HTTP 429 `Retry-After` backoff, fallback routing, and usage cost logging |
| `tests/test_orchestrator.py`, `tests/test_database.py`, `tests/test_recovery.py` | Full 16-agent pipeline, lifecycle state machine, crash recovery, stale lock reclamation, and worker auto-stop |
| `tests/test_live_integrations.py` | Opt-in live tests (`RUN_LIVE_TESTS=1`) for real Gemini/OpenAI/Anthropic APIs, Telegram `getMe`, and GitHub remote connectivity |

---

## 2. Run Opt-In Live Integration Tests

When real credentials are configured in `.env`, run the live verification suite on your phone or workstation:

```bash
RUN_LIVE_TESTS=1 .venv/bin/pytest tests/test_live_integrations.py -v
```

Each live test automatically skips with an explicit reason if its corresponding token (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, or `GITHUB_TOKEN`) is not set.

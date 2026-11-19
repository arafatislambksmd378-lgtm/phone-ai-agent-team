# Integration Test Report (2026-11-19)

## Test environment

- Python 3.11.2, pytest 9.2.0 (installed into the ignored `.venv/`), Linux sandbox.
- No external credentials, no network calls to AI/Telegram/GitHub, no Android SDK, no emulator.
- Command executed: `.venv/bin/python -m pytest -q -o addopts=''`

## Results

| Run | Passed | Failed | Skipped | Xfailed | Notes |
|---|---:|---:|---:|---:|---|
| Baseline (`eec0f11`) | 195 | 0 | 26 | 8 | Before this branch's changes |
| After implementation | **205** | **0** | **26** | **8** | +10 new tests in `tests/test_task_control_and_memory.py` |

No test was counted as passed if skipped or xfailed.

### Why tests are skipped / xfailed

- **22 skipped** — `tests/test_isolation_worker.py`: `ISOLATED_WORKER_ENABLED` is unset. The worker
  stays fail-closed by design; these are not passive omissions but an intentional safety gate.
- **4 skipped** — `tests/test_live_integrations.py`: requires `RUN_LIVE_TESTS=1` plus
  `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `TELEGRAM_BOT_TOKEN`.
- **8 strict xfailed** — legacy expectations that assume host code execution; `tests/conftest.py`
  marks them xfail so an unexpected pass signals that the execution boundary changed and needs review.

## New coverage in this branch (`tests/test_task_control_and_memory.py`)

1. v5→v6 SQLite migration preserves legacy rows and leaves old tasks schedulable.
2. Queued-task cancellation is durable across reopen and cannot be claimed.
3. Running-task cancellation wins over crash recovery (no resurrection).
4. Retry backoff `not_before` is enforced by the atomic claim query.
5. Manual retry/resume rules: COMPLETED is immutable; manual retry resets the attempt budget.
6. Memory dedupe, versioning, history, search, project isolation and retention pruning.
7. Engine task control writes a sync event visible to other interfaces.
8. Telegram `/task` command controls the same row and records `source=telegram`.
9. Authenticated API task control + memory endpoints, idempotent duplicate POST, 401 without token.
10. Owner cancellation of the last task marks the project CANCELLED, never COMPLETED.

## Cross-interface synchronization evidence

`test_api_control_and_memory_endpoints_are_auth_gated_and_idempotent` writes the control action
through the HTTP API used by Android and asserts the row in `task_control_events` and the response
of `GET .../tasks/{id}`. `test_telegram_task_command_controls_one_task` drives the Telegram handler
against the same engine/database and asserts the `task_cancel` sync event. Both tests read the same
SQLite rows, which is the synchronization mechanism shared by Telegram and Android.

**This is backend-state synchronization evidence, not live Telegram delivery or a physical Android
device test.** Both directions are implemented against the shared authoritative state, but a real
bot token/authorized user and a built app are required for an end-to-end device test.

## Other commands executed

- `.venv/bin/python -m compileall -q agents config execution integrations memory orchestrator security main.py` — passed.
- `git diff --check` — passed.
- `TELEGRAM_BOT_TOKEN=123:ABC TELEGRAM_ALLOWED_USER_ID=1 .venv/bin/python main.py --selfcheck` —
  "self check passed"; AI mode reported honestly as `mock` because no provider key is configured.

## Not verified (blockers)

- Live AI provider responses, live Telegram Bot API, live GitHub write lifecycle: credentials and a
  dedicated test repository were not supplied.
- Android APK build, instrumentation tests and screenshots for this commit: no Android SDK, Gradle
  or emulator available locally. Earlier CI evidence for a *different* commit is recorded in
  `docs/implementation/PHASE41_STATUS_AND_VALIDATION.md` and must not be reused for this branch.
- Isolated worker escape/adversarial audit: `ISOLATED_WORKER_ENABLED` remains false.

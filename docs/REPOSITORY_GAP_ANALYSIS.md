# Repository Gap Analysis (Phase 1 + Phase 2)

**Date:** 2026-11-19 · **Audited commit:** `eec0f11` (current `origin/main`, PRs #1–#5)
**Implementation branch:** `arena/3d5b512c-phone-ai-agent-team` (fast-forwarded to `origin/main` before work)
**Method:** direct source inspection, `git log`, `gh pr list`, and an actual local test run
(`.venv/bin/python -m pytest -q -o addopts=''` → **195 passed, 26 skipped, 8 xfailed**).
No older audit report was treated as evidence of the current state.

## 1. What actually exists (verified in code)

| Area | Evidence in source | Classification |
|---|---|---|
| 16 logical agents | `agents/registry.py` registers exactly 16 IDs; each has `agent.py`, `prompts.py`, `BaseAgent.contract()` | Implemented; 3 infra roles replace original roles (see §3) |
| Dynamic planning / DAG | `orchestrator/planner.py` (781 LOC): blueprint classification, parallel branches, cycle checks, `replan()` | Implemented |
| Orchestrator | `orchestrator/engine.py` (825 LOC): project locks, handoffs, approval gates, resource admission, timeout, completion | Implemented |
| Persistence | `memory/database.py` (844 LOC), schema v5, WAL, atomic `claim_task`, dependency join, migrations, backups | Implemented |
| GitHub | `integrations/github/{api,client,executor,diff,repository_context}.py`; API routes for branches/files/PRs/diffs/merge | Implemented locally; live mutations unverified |
| Telegram | `integrations/telegram/{bot,handler}.py`; auth, callbacks, uploads, approvals, ZIP delivery, `/sync` | Implemented; live API unverified |
| Android | `android/app/.../MainActivity.kt` + `ApiClient.kt`: shared backend, repository browser/editor, PRs, approvals | Implemented (partial screen coverage) |
| AI router | `integrations/ai_providers/router.py`; Gemini/OpenAI/Codex/Anthropic, fallback, token/cost records | Implemented; live provider calls unverified |
| Execution isolation | `execution/isolation.py` + `execution/worker/*` (namespace/Landlock/seccomp/rlimits/adversarial probe); fail-closed default | Implemented, **disabled by default**; no independent escape audit |
| Approval enforcement | `security/approval.py` + gates in engine, FileManager, GitHub executor, API | Implemented; audited by `tests/test_phase3_security.py` |
| CI | `.github/workflows/{python-tests,android,android-emulator}.yml` | Present |
| Test suite | 229 collected: 195 pass, 26 skip (22 fail-closed worker, 4 opt-in live), 8 strict xfail (blocked host execution) | Verified locally |

## 2. Verified gaps (this analysis)

| ID | Severity | Gap | Code evidence | Action in this branch |
|---|---|---|---|---|
| GAP-01 | **P0** | Task cancellation is process-local only. `OrchestratorEngine.cancel()` adds to `_cancelled` in memory and `cancel()`/`recover()` mutate project state; after a backend restart a cancelled task can be reclaimed by `Database.claim_task()`. | `orchestrator/engine.py::_execute_task`, `memory/database.py::claim_task` | Fixed: persisted `cancel_requested` + worker-side cooperative cancellation |
| GAP-02 | **P0** | Failed tasks are requeued in a tight loop with no backoff. `_execute_task` calls `set_task_status(..., "QUEUED")` immediately after every retry; `claim_task` has no not-before check. This can burn API quota / hammer GitHub on rate limits. | `orchestrator/engine.py` failure branch, `claim_task()` | Fixed: exponential `not_before` delay, obeyed atomically by the queue |
| GAP-03 | **P1** | No per-task retry/resume/cancel control endpoint. Required Task Manager operations only exist at project scope (`/control`, `/recover`), so Android/Telegram cannot act on one task. | `integrations/api/server.py`, `MainActivity.kt`, Telegram HELP | Fixed: authenticated `POST .../tasks/{id}/control` + Telegram `/task` + Android Tasks screen |
| GAP-04 | **P1** | Control requests had no idempotency key, so a retried HTTP request could double-apply a control action (duplicate sync records). | `request_idempotency` table existed but unused for task control | Fixed: key-based dedupe for control requests |
| GAP-05 | **P1** | Project memory is a single JSON blob (`project_memory.facts`). No versioning, source attribution, search, dedupe, retention, or memory of real task results. | `memory/database.py::set_memory`, `memory/project_memory.py` (18 LOC) | Fixed: additive `memory_entries` table with SHA-256 dedupe, versions, supersession, search, retention; real task outcomes recorded |
| GAP-06 | **P1** | Original 16-role spec names a **Project Manager** and an **Android Developer**; the registry instead has `chief_orchestrator`, `api_integrator`, `workspace_executor`. No explicit role mapping is exposed to clients. | `agents/registry.py`, README agent table | Documented mapping; no destructive rename (must not break pipelines/tests/history) |
| GAP-07 | **P2** | Android has no dedicated Task Manager or Project Memory screen; tasks appear only in Dashboard/agents counters. | `MainActivity.kt::show()` | Fixed: Tasks and Memory screens wired to real backend endpoints |
| GAP-08 | **P1** | Full end-to-end AI→code→PR workflow and live Telegram/Android sync cannot be verified here: no model/Telegram credentials, no dedicated test repository, no isolated-worker escape audit. | `execution/isolation.py` fail-closed; `docs/implementation/PHASE41_STATUS...md` | Honestly reported as blocked; no fake success or mock bypass |
| GAP-09 | **P1** | Android APK build cannot be produced locally (no Android SDK/Gradle); only CI artifacts exist from earlier commits. | no `gradlew`, no SDK in sandbox | Reported with exact prior CI evidence; not claimed for this commit |
| GAP-10 | **P2** | Some documentation duplicates/contradicts current code (e.g. 184 vs. actual 195 passing tests, agent-role names). | `docs/PRODUCTION_READINESS.md`, README | Updated in this branch; historical docs kept with explicit status labels |

## 3. Agent-role traceability (original 16 → implementation)

| Original logical role | Implementation | Status |
|---|---|---|
| Project Analyst | `requirement_analyst` | Implemented |
| Software Architect | `software_architect` | Implemented |
| **Project Manager** | `chief_orchestrator` + `orchestrator/planner.py` (task decomposition/priorities/dependencies) | Responsibility covered, **distinct role not registered** (GAP-06) |
| UI/UX Designer | `uiux_designer` | Implemented |
| Frontend Developer | `frontend_developer` | Implemented |
| Backend Developer | `backend_developer` | Implemented |
| **Android Developer** | `software_architect` + `devops` generate Kotlin/Gradle projects for `android_app` blueprints | Responsibility covered, **distinct role not registered** (GAP-06) |
| Database Engineer | `database_engineer` | Implemented |
| AI/ML Engineer | `api_integrator` + shared `ModelRouter` | Partial: no standalone model-training role (out of scope for generated apps) |
| QA Engineer | `qa_tester` | Implemented (host test execution still fail-closed without isolation) |
| Security Engineer | `security_engineer` | Implemented |
| Code Reviewer | `code_reviewer` | Implemented |
| DevOps Engineer | `devops` | Implemented |
| Documentation Agent | `documentation` | Implemented |
| Debugging Engineer | `debugger` | Implemented |
| Release Manager | `release_manager` | Implemented |
| *Infrastructure (kept)* | `api_integrator`, `workspace_executor`, `chief_orchestrator` | Preserved as supporting roles |

## 4. Severity order used for implementation

1. **P0** — GAP-01 (durable cancellation), GAP-02 (retry backoff).
2. **P1** — GAP-03/04 (task control + idempotency), GAP-05 (versioned memory).
3. **P2** — GAP-07 (Android screens), GAP-10 (documentation accuracy).
4. **Blocked / not claimable here** — GAP-06 (would require renumbering 16-role pipeline), GAP-08/09 (credentials, dedicated test repo, Android SDK, independent isolation audit).

Implementation and verification evidence are recorded in `docs/IMPLEMENTATION_STATUS.md`
and `docs/INTEGRATION_TEST_REPORT.md`; the final acceptance matrix is in
`MASTER_GAP_IMPLEMENTATION_FINAL_REPORT.md`.

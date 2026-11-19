# Master Batch 02 — Live AI Provider & Telegram Integration Report

**Date:** 2026-11-19 · **Branch:** `arena/3d5b512c-phone-ai-agent-team` · **PR:** [#6](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/pull/6) (OPEN, not merged)
**Final successful run:** [`44466025806`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44466025806), head `95b188b6ddef800f577137c13b5b3ead47c5597d`
**Overall status:** **LIVE VERIFICATION PASSED (with documented limitations).** No merge, deployment, paid activation or production mutation was performed.

All live claims below come from GitHub Actions runner evidence. No credential value was received, stored, committed, printed or pasted into chat; only masked status hints are reproduced here.

## 1. Credentials and authorization (masked status only)

| Item | Live run status (masked) | Where configured |
|---|---|---|
| `GEMINI_API_KEY` | PASS — `AQ.A••••JVg` | GitHub Actions secret |
| `TELEGRAM_BOT_TOKEN` | PASS — `9065••••4EM`, shape `looks_like_bot_token` | GitHub Actions secret |
| `TELEGRAM_ALLOWED_USER_ID` | PASS — `9201••••233` | GitHub Actions secret |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` (owner-confirmed no-charge; repository variable still overrides) | workflow default |
| Owner `free_tier_confirmed` | PASS | workflow input / push-mode override |

The sandbox token could not read secret names (`403`) or Actions variables (`403`); no value was requested. The setup portal (`scripts/start_web_setup.sh`, `0.0.0.0:8080` plain HTTP without bearer header) was **not** used. OpenAI, Codex and Anthropic were never enabled; no spending limit was changed.

## 2. Workflow runs and exact failure triage

| Run | Result | Exact finding / fix |
|---|---|---|
| [`44452845982`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44452845982) | FAIL | Baseline tests inherited live env/secrets. Fixed by clearing secrets and setting `RUN_LIVE_TESTS=0` in the baseline step. |
| [`44453029152`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44453029152) | FAIL | Real Gemini step failed; no captured logs. Added `tee` log capture and PR evidence mirror. |
| [`44453193262`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44453193262) | FAIL | `gemini-2.0-flash` → HTTP 404 "no longer available"; provider 3/6. Model catalog query added (read-only, no generation charge). |
| [`44453360922`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44453360922) | FAIL | Catalog confirmed available names; `gemini-2.0-flash` still 404. Owner chose `gemini-2.5-flash` as no-charge. |
| [`44456560697`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44456560697) | FAIL | `gemini-2.5-flash` → HTTP 404 "no longer available to new users" despite being catalog-listed; API suggested `gemini-3.8-flash`. Provider 4/7. |
| [`44456719128`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44456719128) | FAIL | Telegram `getMe`/`sendMessage`/`sendDocument` all HTTP 404; script falsely reported 12/13 because attempted sends were recorded as delivered. Fixed harness to fail closed on any `ok:false` transport result. |
| [`44457575906`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44457575906) | FAIL | Preflight HTTPS 404 cause traced: stored Telegram value failed token-shape check (not `<digits>:<hash>`). Owner replaced the secret with the full BotFather token. Added masked shape check; fail-closed Telegram result 1/13. |
| [`44465283838`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44465283838) | FAIL | Real Telegram auth/messages/sync passed. Two exact failures: (a) real agent step HTTP 503 "model currently experiencing high demand"; (b) `answerCallbackQuery` HTTP 400 for synthetic callback ids. Provider 6/7, Telegram 10/13. |
| [`44465685790`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44465685790) | PASS | All steps green after transient 503 cleared and synthetic callback acknowledgements were recorded locally (clearly labeled, never claimed as delivered). |
| [`44466025806`](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44466025806) | PASS | Final evidence run: provider 7/7, Telegram 13/13, baseline suite and live pytest green. Redacted summary mirrored to PR #6. |

Log/artifact download from the sandbox fails with blob-storage `EOF`, so the final evidence was mirrored to a redacted PR #6 comment by the workflow. Earlier bot comments that leaked a derived allowed-user ID in a handler log line were deleted; `integrations/telegram/handler.py` now logs `identity withheld`, and `scripts/live_telegram_check.py` no longer prints audit rows.

## 3. AI provider connection results (LIVE)

From run `44466025806`, `live-evidence/provider.md`, **7/7 PASS**:

- Read-only model catalog query (no generation charge) — models listed, including `gemini-3.5-flash-lite`.
- Provider health: `mode=live model=gemini-3.5-flash-lite fallback=False`.
- Real completion: `provider=gemini model=gemini-3.5-flash-lite mode=live`, tokens `11/1`, `usage_cost_usd=1.5e-06`, response `READY`.
- Invalid key rejected by real API: `BAD_REQUEST`, HTTP 400.
- Timeout classification: `TIMEOUT` with **mocked** transport (no live timeout induced).
- Provider fallback: primary invalid key HTTP 400 → `gemini_backup` recovered, `mode=live`.
- Real agent execution: `task_39c58a1c7b42 status=COMPLETED agent_mode=live provider=gemini usage_rows=1 memory_entries=1 handoffs=1`.

Rate-limit (HTTP 429) and real timeout induction were **not** performed live; those paths remain covered by mocked-transport tests. The earlier HTTP 503 was a transient provider capacity error, not a code defect; it cleared on retry.

## 4. Telegram Bot connection results (LIVE)

From the same run, `live-evidence/telegram.md`, **13/13 PASS**:

- Real `getMe` returned `ok=True` and `@PhoneAIAgentTeamBot` (bot id withheld from this report).
- Unauthorized message rejected, no project created; unauthorized callback rejected, approval stayed `PENDING` with an audit entry; identities withheld in logs.
- `/start`, `/help`, `/new`, `/status`, `/tasks`, `/agents`, `/pending` executed over real `sendMessage` transport.
- Approval state transitions and duplicate-callback protection verified against the shared database; duplicate callback ignored.
- Artifact ZIP delivered with real `sendDocument`; memory written via API appeared in Telegram `/sync` activity.
- Optional `/build` skipped (`run_agent_build=false`), correctly reported as SKIPPED, not PASS.

Limitation: injected updates carry synthetic callback/message ids; Telegram rejects `answerCallbackQuery` for unknown ids (HTTP 400 observed in run `44465283838`). The harness records those acknowledgements **locally**, labels them as such, and does **not** claim real button-press delivery. A real acknowledgement requires a human pressing an inline button in the authorized chat.

## 5. Model Router verification

Mocked/local: provider health shape, fallback policy, per-provider model config, secret redaction in `repr()`, honest `live`/`mock`/`failed` modes. Live: health `live`, invalid key `BAD_REQUEST` 400, fallback recovered over real HTTP, usage/cost row attributed to the project. Two defects fixed earlier: authoritative router provider attribution and `project_id` on usage records (regression test `tests/test_router.py::test_usage_records_project_id_and_successful_provider_is_router_authoritative`).

## 6. Android ↔ Telegram shared-state synchronization (LIVE)

Real Bot API transport plus one local API server, one `OrchestratorEngine` and one SQLite database on the runner:

- Project created by Telegram → visible via the Android-style `/api/projects` list.
- Project created via the API → visible in Telegram `/status`.
- Task created via API → cancelled from Telegram → `CANCELLED` through the API with a `task_cancel` sync event.
- Approval created via API → decided by Telegram callback → same row visible through the API with sync event.
- Memory written via API → visible in Telegram `/sync`; project/task/agent/approval/memory/activity share one DB and backend.

This is **backend-level two-way synchronization**, not a test on a physical Android device or emulator. No Android toolchain was available in the sandbox; no APK/device claim is made.

## 7. Real agent execution evidence (LIVE)

Run `44466025806`: a harmless `requirement_analyst` task on a throwaway `LiveAgentCheck` project completed with a real `gemini-3.5-flash-lite` generation. The produced requirements JSON had `mode=live` and `provider=gemini`; one project-attributed usage row, one `task_result` memory entry and one verified handoff were recorded. Token/cost evidence: `11` prompt + `1` completion tokens, `1.5e-06 USD` for the simple completion check. No production repository was mutated by an agent.

## 8. Test results — mocked and live kept separate (A–G)

| Category | Result | Live or mocked | Evidence |
|---|---|---|---|
| A. Local automated suite | **206 passed, 0 failed, 26 skipped, 8 xfailed** | Local, no live calls | `live-evidence/local-tests.log`, run `44466025806` |
| B. Real Gemini provider API | **PASS 7/7** | Live | `live-evidence/provider.md`, run `44466025806` |
| C. Real Telegram Bot API | **PASS 13/13** | Live transport; synthetic incoming updates | `live-evidence/telegram.md`, run `44466025806` |
| D. Android ↔ Telegram sync | **PASS** | Live Bot API + local API/SQLite; not a device test | same Telegram report |
| E. Real agent execution | **PASS** | Live generation via orchestrator | provider report, task `task_39c58a1c7b42` |
| F. Security tests | **PASS** | Fail-closed preflight, unauthorized user/callback, redaction, shape check; local suite | run `44466025806` |
| G. GitHub integration tests | PASS locally (existing suite) | Mocked GitHub transport; only workflow runs and PR evidence comments touched this repo | prior suite + PR #6 evidence |

Also verified locally: `compileall` on changed modules/scripts, `git diff --check`, and a mocked Telegram dry run (13/13, explicitly not live).

## 9. Security and no-paid verification

- No secret value in chat, issue, PR, source, logs or report; only masked hints from the runner.
- Credentials remain in GitHub Actions secrets; local storage convention is `.env` with mode `0600`. No `.env` was created in the sandbox.
- Workflow scopes were minimized; the temporary comment-mirroring and push trigger are removed before finalization. Live steps are gated on explicit no-charge confirmation.
- Only the owner-confirmed no-charge Gemini model was used. No OpenAI/Codex/Anthropic key, no spending-limit change, no paid service activation.
- API bearer auth, Telegram allowed-user checks, approval policy, task idempotency and 0600 secret storage remain unchanged.

## 10. Remaining limitations

1. A real inline-button acknowledgement from a human press was not verified; synthetic callback ids are acknowledged locally by the harness.
2. HTTP 429 rate limiting and real transport timeouts were not induced live; only mocked-transport classifications were exercised.
3. `/build` with live AI was not enabled (`run_agent_build=false`) to limit call volume; a full live pipeline remains optional.
4. Android verification was at the shared backend/API level, not a device/emulator run.
5. The sandbox cannot download Actions logs/artifacts (blob `EOF`); evidence was mirrored as a redacted PR comment instead.

## 11. Git deliverables and next owner action

- Branch: `arena/3d5b512c-phone-ai-agent-team`; PR [#6](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/pull/6) — **OPEN, not merged**.
- Model defaults updated from retired `gemini-2.0-flash` to owner-confirmed `gemini-3.5-flash-lite` in config, docs and tooling; repository variable `GEMINI_MODEL` still wins if set.
- Temporary push trigger, sentinel file and PR-evidence mirror step removed after the final evidence run; the workflow is manual `workflow_dispatch` only.
- See [`docs/LIVE_INTEGRATION_SETUP.md`](docs/LIVE_INTEGRATION_SETUP.md) for secret rotation/restart instructions. Merge and deployment require explicit owner approval.

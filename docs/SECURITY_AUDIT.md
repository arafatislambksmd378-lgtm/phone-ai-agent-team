# Security Audit — Gap Remediation Branch (2026-11-19)

Scope: this branch's changes plus the security boundary they touch. This is a source-level
audit backed by local tests; it is **not** a penetration test or an independent isolation audit.

## Verified controls

| Control | Implementation | Verification |
|---|---|---|
| API authentication never disabled; bearer risk: constant-time comparison; failed-auth rate limit; 2 MB JSON body cap; no-store + CSP/HSTS headers | `integrations/api/server.py` | `tests/test_phase3_security.py` (incl. weak-token refusal before bind) |
| Project-scoped task control and memory; cross-project task IDs return 404 | `integrations/api/server.py`, `orchestrator/engine.py` | `test_api_control_and_memory_endpoints_are_auth_gated_and_idempotent`, `test_engine_control_task_writes_shared_sync_events` |
| Idempotency keys for task creation and task control; repeated taps cannot double-apply | `memory/database.py::request_idempotency`, API routes | API test asserts a single `cancel_requested` event after duplicate POST |
| Cancellation cannot be lost on backend restart (persisted flag, honoured by claim/recover) | `memory/database.py`, `orchestrator/engine.py` | `test_cancel_running_task_survives_crash_recovery` |
| Completed tasks cannot be retried, preventing duplicate commits/PRs from a rerun | `requeue_task()` | `test_retry_and_resume_rules_prevent_duplicate_completed_work` |
| Approval TTL, single-use consumption, audit log, paid-spend gate, public-repo push gate | `security/approval.py`, engine/FileManager/GitHub executor | `tests/test_approvals.py`, `tests/test_phase3_security.py` |
| ZIP traversal/symlink/ratio/size caps; document extraction with stdlib only; path confinement; secret redaction | `security/validation.py`, `execution/document_processor.py` | `tests/test_security.py`, `tests/test_document_and_github.py` |
| Untrusted code execution fails closed unless a verified isolated worker passes an adversarial probe | `execution/isolation.py`, `execution/worker/*` | 22 tests skip by default; strict xfails document blocked host paths |

## New surface introduced in this branch

- `POST .../tasks/{tid}/control` is authenticated like every other API route. It accepts only
  `cancel`, `retry`, `resume`; all actions are written to `task_control_events` and `/api/sync`.
  `actor` is client-supplied but capped at 80 chars and is an audit label, not an authorization
  decision — authorization remains the single API bearer token (single-owner design).
- `POST .../memory` accepts bounded `kind`/`key`/`value` strings and writes a project-scoped row;
  `GET .../memory` filters by `kind`/free-text query. Values are plain project knowledge, never
  executed. No HTML rendering of memory content is done by the backend.
- Memory retention prunes only superseded versions beyond `MEMORY_VERSIONS_PER_KEY` (default 25);
  the active version is always kept. There is no cross-project memory query path.

## Remaining risks / honest gaps

1. **No user table.** The product has one human owner; authorization is the API token plus the
   Telegram allowed-user ID. Multi-user isolation is therefore not implemented — project memory
   isolation is by `project_id` only. Do not expose the API to multiple mutually untrusted users.
2. **No independent adversarial security review of the worker.** `ISOLATED_WORKER_ENABLED` is off
   by default; do not enable it on the basis of this audit alone. 22 worker tests skip and 8
   host-execution expectations remain strict xfails.
3. **Android sources not compiled here.** The new Tasks/Memory screens use the same authenticated
   client as existing screens; verify the build with GitHub Actions before distribution.
4. **Secret scanning is heuristic.** GitHub push pre-checks reduce but cannot eliminate the risk
   of leaking secrets in generated code; keep tokens out of prompts and review diffs in the app.
5. **Memory values are untrusted text.** They are displayed in the Android app; the app uses
   `TextView` (no HTML parsing), which avoids the common injection path, but very large values are
   truncated at the API/DB boundary (20 KB) and UI (240 chars).
6. **Control idempotency cache TTL is 600s**; after expiry a repeated key can apply again. The DB
   task state still prevents double execution of a completed task, but clients should generate a
   fresh unique key per user gesture (the Android app does).

No production secrets, private repository data or credentials were accessed or committed while
performing this work.

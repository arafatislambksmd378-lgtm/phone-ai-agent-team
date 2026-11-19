# Implementation Roadmap

This roadmap governs the 11-phase engineering upgrade of `phone-ai-agent-team` from a fixed-template 16-step pipeline into a dynamic, secure, resource-aware, multi-workflow 16-Agent AI Development System tailored for Android 14 (3 GB RAM / 64 GB storage / Termux).

---

## Phase 1: Repository Audit & Baseline Verification (Completed)
- Full source-code inspection across `agents/`, `orchestrator/`, `execution/`, `integrations/`, `memory/`, `security/`, `config/`, `scripts/`, `docs/`, and `tests/`.
- Baseline test run verified (`136 passed`).
- Created `docs/COMPREHENSIVE_AUDIT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, and `docs/REQUIREMENT_TRACEABILITY.md`.

## Phase 2: Critical Architecture & 16-Agent Intelligence System
- Upgrade `agents/base.py` and `agents/helpers.py`:
  - Structured `AgentContract` (input schema, output schema, allowed tools, permission boundaries, completion criteria).
  - Context budget manager (`build_context`) keeping prompts memory-safe and token-bounded on 3 GB RAM.
  - Output normalization (`extract_code_block`, `extract_json_block`) so Markdown-fenced LLM outputs compile cleanly.
  - Per-agent execution logging and output validation.
- Wire every `agents/<role>/prompts.py` into its corresponding `agent.py` with rich, role-specific system prompts and user prompt builders.
- Upgrade all 16 Agents so every role genuinely leverages the AI Model Router when configured while retaining verified category-aware fallbacks when in offline `mock` mode.

## Phase 3: Dynamic Blueprint Planning & Agent-to-Agent Handoffs
- Implement `BlueprintAnalyzer` + upgraded `TaskPlanner` (`orchestrator/planner.py`):
  - Classify software type (`web_app`, `rest_api`, `backend_service`, `cli_tool`, `python_library`, `database_app`, `android_app`, `desktop_app`) and project complexity (`simple`, `moderate`, `complex`).
  - Extract functional/non-functional requirements, entities, and target tech stack.
  - Dynamically select required Agents and exclude irrelevant roles (e.g., skip `uiux_designer` and `frontend_developer` for a CLI tool or pure REST API; keep full 16-agent mode available when requested or for full-stack builds).
  - Build a true dependency-aware Task DAG with parallelizable branches where safe, explicit task inputs, outputs, priorities, and acceptance criteria.
  - Implement `replan()` when new requirements arrive or execution results require repair/adaptation.
- Implement `HandoffManager` (`orchestrator/handoff.py`):
  - Persist structured task outputs (`data`, `files`, `summary`, `mode`, `artifacts`) in SQLite and `ProjectMemory`.
  - Inject upstream dependency outputs into downstream task payloads automatically.
  - Propagate permanent upstream failures (`SKIPPED` / blocked-by-dependency) so workers never deadlock.

## Phase 4: Model Router & Real AI Execution Integration
- Upgrade `ProviderAdapter` & `ModelRouter`:
  - Configurable `max_tokens` (raised default from `2048` to `4096` to prevent code truncation).
  - `Retry-After` HTTP header parsing on `429 RATE_LIMITED`.
  - Extensible adapter registry (`register_provider`) supporting custom OpenAI/Codex-compatible endpoints alongside Gemini and Anthropic.
  - Structured JSON/file-manifest generation helpers so Developer Agents can create and modify multiple project files cleanly.

## Phase 5: Project-Aware Software Development Workflows
- Support tailored architecture, implementation, database, DevOps, QA test generation, debugging, documentation, and release readiness gates for:
  - **Full-Stack Web Applications** (Frontend SPA + REST API + SQLite WAL)
  - **REST APIs & Backend Services** (Headless JSON API + domain logic + tests, no unnecessary frontend)
  - **CLI Tools & Python Software/Libraries** (`argparse` CLI / modular Python package + unit & CLI tests)
  - **Database-Driven Applications** (Normalized multi-table SQLite schema, migrations, repository layer)
  - **Android Applications** (Kotlin + Gradle project structure, `AndroidManifest.xml`, ViewModel/domain logic, host-verifiable unit tests, honest Termux SDK limitation detection & optional approved CI workflow)
  - **Desktop Applications** (Cross-platform Python `tkinter` + headless presenter/controller architecture testable without X11/Wayland on Termux)
- Upgrade `QA & Testing Agent` and `Debugging & Code Repair Agent` to generate and execute project-appropriate `pytest` suites, inspect real tracebacks, apply minimal repairs (AI + deterministic syntax/import/contract repair), and re-run regression tests.

## Phase 6: Approval Enforcement, Security & Document Processing
- Upgrade `Database` (Schema v3) and `ApprovalManager`:
  - Approval expiration (`expires_at`), task/project binding, single-decision atomic lock, duplicate callback rejection, and `approval_audit_logs` table.
  - Enforce approval checks directly in `CommandRunner`, `FileManager` (destructive deletes / protected files), `GitHubClient` (push / public release), and `OrchestratorEngine` (paid threshold / production deploy / destructive DB ops).
- Implement `execution/document_processor.py` & harden `security/validation.py`:
  - Genuine text extraction for `.txt`, `.md`, `.json`, `.csv`, `.pdf` (zlib FlateDecode stream + PDF text operator parser), `.docx` (`word/document.xml` XML parser), and source code files.
  - Harden `safe_extract_zip()` against POSIX symlink entries (`S_IFLNK`), archive file size limits, file count caps, decompressed size caps, compression ratio bombs, and suspicious nested archives.

## Phase 7: GitHub Integration & Artifact Packaging
- Upgrade `integrations/github/client.py` and `execution/sandbox.py`:
  - Local Git init, branch creation/switching, automatic task checkpoints, checkpoint rollback/restore.
  - Remote configuration, secret scanning (`scan_workspace_for_secrets`) before commit/push, public-repository detection requiring explicit human approval, authenticated push/pull, conflict detection, and backup verification.
- Implement clean project ZIP artifact packaging (`artifacts/<project>.zip`) excluding `.git`/`__pycache__`/secrets, SHA-256 recorded in `artifacts` table.

## Phase 8: Android Resource Manager & Lifecycle/Recovery Hardening
- Implement `execution/resource_manager.py`:
  - Monitor Android/Linux RAM (`/proc/meminfo` + process RSS), CPU load, and storage (`shutil.disk_usage`).
  - Enforce default 1 active worker; allow 2nd worker *only* when available RAM, CPU, and disk thresholds pass.
  - Integrate `ProcessManager` into `CommandRunner` and `QA/Debugger` subprocesses with strict timeouts, output caps, and graceful cancellation.
  - Automatic temp-file cleanup (`tg_upload_*`, `*.part`, `*.tmp`) and post-task `gc.collect()`.
- Harden `Database` and `OrchestratorEngine`:
  - Implement `stale_after` lock reclamation in `claim_task()`, per-task timeout enforcement, interrupted-task detection on startup, and graceful worker pool auto-shutdown on idle.

## Phase 9: Telegram Bot Completion
- Fix `_cmd_logs` SQL tuple bug.
- Upgrade `TelegramBot` & `TelegramHandler`:
  - Support `callback_query` updates, inline approval keyboards (`✅ Approve` / `❌ Reject`), `answerCallbackQuery`, duplicate click handling, and strict `TELEGRAM_ALLOWED_USER_ID` verification on both messages and callbacks.
  - Fix `/new` workflow: support `/new <name> <blueprint>` immediate start OR `/new <name>` followed by blueprint text/file upload + `/build` (and automatic blueprint trigger), while keeping full backward compatibility.
  - Wire live progress notifications, error alerts, final completion reports, and project ZIP artifact delivery (`sendDocument` + `/artifact` / `/report`).

## Phase 10: Termux Installation & Complete Documentation Suite
- Add `scripts/stop_termux.sh` and refine `scripts/install_termux.sh`, `scripts/start_termux.sh`, `scripts/backup.sh`, `scripts/restore.sh`, and `.env.example` (repeatable, safe, non-destructive).
- Write/update `README.md`, `docs/INSTALLATION.md`, `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_GUIDE.md`, `docs/SECURITY.md`, `docs/RECOVERY.md`, `docs/TESTING.md`, and `docs/PRODUCTION_READINESS.md`.

## Phase 11: Full Integration, Regression Testing & Readiness Assessment
- Comprehensive unit, contract, security, persistence, recovery, resource, Git, Telegram, dynamic planner, multi-category workflow, and end-to-end tests (+ separate opt-in live provider integration test module).
- Run full test suite, CLI self-checks, and offline multi-category demos; record exact verified metrics and honest status labels.

---

## Phase 12 — Master-prompt gap remediation (executed 2026-11-19)

1. **Inspection** — current `origin/main` (`eec0f11`) integrated into this branch; no earlier audit trusted.
2. **Gap analysis** — `docs/REPOSITORY_GAP_ANALYSIS.md` (10 gaps, P0→P3).
3. **P0 fixes** — persisted cancellation and exponential retry backoff in SQLite + engine.
4. **P1 fixes** — task control API/Telegram/Android, idempotency, versioned structured memory.
5. **P2 fixes** — Android Tasks and Memory screens bound to real endpoints; documentation refreshed.
6. **Verification** — 205 passed / 0 failed / 26 skipped / 8 xfailed; compileall + diff check + selfcheck.
7. **Remaining** — live integrations, isolated-worker audit, APK build and distinct PM/Android agent
   roles remain blocked or deliberately unchanged; see `docs/IMPLEMENTATION_STATUS.md`.

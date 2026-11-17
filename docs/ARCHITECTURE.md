# Safety and currency notice

This is a historical architecture sketch, not evidence of a functioning isolation boundary. Host and GitHub agent execution are currently blocked pending an independently verified remote worker. See `implementation/PHASE41_ISOLATED_WORKER_REQUIREMENTS.md` and the latest Phase 4.1 report.

# System Architecture

## 1. Design Philosophy: On-Demand Logical Agents on 3 GB RAM

Running 16 persistent Python processes or local LLMs on a 3 GB RAM Android 14 smartphone would immediately exhaust memory and trigger Android's Phantom Process Killer. Instead, `phone-ai-agent-team` separates **listening**, **state**, and **execution**:

1. **Always-On Lightweight Listener (~26 MB RSS)**: `TelegramBot` uses Python's standard-library `urllib.request` long-polling (`getUpdates`) with zero async framework overhead.
2. **SQLite WAL State & Queue (`memory/database.py`, Schema v3)**: All projects, DAG tasks, multi-parent dependencies, verified handoff envelopes, approvals, audit logs, Git checkpoints, and token costs live in a single WAL-mode SQLite file.
3. **On-Demand Worker Pool (`orchestrator/scheduler.py`)**: Starts when tasks enter `QUEUED`, runs **1 worker thread by default** (optionally 2 when `ResourceManager` confirms ≥ 600 MB free RAM and low CPU load), instantiates the target logical `BaseAgent` subclass only for the duration of a single task, runs `gc.collect()` + temporary artifact cleanup afterward, and stops all worker threads automatically when the queue empties.

---

## 2. End-to-End Execution Pipeline

```text
Blueprint (Telegram text / TXT / MD / PDF / DOCX / JSON / CSV / ZIP)
        │
        ▼
[DocumentProcessor + Upload/ZIP Validator]  ──► Untrusted data extracted safely to requirements.txt
        │
        ▼
[BlueprintAnalyzer + TaskPlanner]           ──► Detects software_type, FRs/NFRs, tech stack;
        │                                       excludes irrelevant agents; validates acyclic Task DAG
        ▼
[SQLite Task Queue + LifecycleManager]      ──► INPUT_RECEIVED → VALIDATING → PLANNING → QUEUED
        │
        ▼
[ResourceManager + ApprovalManager]         ──► Verifies RAM/Disk, Paid-API cap & high-impact gates
        │
        ▼
[AgentRegistry → 16 Logical Agents]         ──► Receives verified HandoffEnvelopes from upstream parents
        │                                       Calls ModelRouter (Gemini / OpenAI / Codex / Anthropic)
        │                                       Historical tools (execution currently blocked; no proven sandbox)
        ▼
[HandoffManager + Git Sandbox]              ──► Verifies output files + SHA-256 digests, commits Git
        │                                       checkpoint, propagates failures to downstream dependents
        ▼
[Deliverable Packager + Telegram Delivery]  ──► Packages clean <project>_deliverable.zip & sends report
```

---

## 3. Dynamic Agent Selection & Task DAG (`orchestrator/planner.py`)

`BlueprintAnalyzer` inspects the blueprint (combining deterministic heuristics with optional AI refinement) to classify the project into one of 8 workflows and select **only the required agents**:

| Software Category | Selected Agents | Excluded Irrelevant Agents |
|---|---|---|
| `web_app` (Full-Stack) | All 16 agents (parallel branches after Architecture, Chief Orchestrator final gate) | None |
| `rest_api` / `backend_service` | 13 agents (`requirement_analyst`, `software_architect`, `database_engineer`, `backend_developer`, `api_integrator`, `security_engineer`, `qa_tester`, `debugger`, `code_reviewer`, `devops`, `documentation`, `release_manager`, `chief_orchestrator`) | `uiux_designer`, `frontend_developer`, `workspace_executor` |
| `cli_tool` / `python_library` | 9–10 agents (Requirements, Architecture, Core Implementation, Security, QA, Debugger, Review, Docs, Release, Chief Gate) | `uiux_designer`, `frontend_developer`, `database_engineer`, `api_integrator`, `devops` |
| `database_app` | Requirements, Architecture, `database_engineer`, `backend_developer`, Security, QA, Debugger, Review, Docs, Release, Chief Gate | `uiux_designer`, `frontend_developer`, `api_integrator` |
| `android_app` | Requirements, Architecture, `uiux_designer`, `backend_developer` (Kotlin/Gradle project + CI workflow), `api_integrator`, Security, QA, Debugger, Review, `devops` (Termux SDK check), Docs, Release, Chief Gate | `frontend_developer`, `database_engineer` |
| `desktop_app` | Requirements, Architecture, `uiux_designer`, `backend_developer` (Tkinter MVC + headless core), Security, QA, Debugger, Review, Docs, Release, Chief Gate | `frontend_developer`, `api_integrator`, `devops` |

Every generated plan is verified with **Kahn's topological sort** before insertion; cyclic or dangling dependencies raise `PlanValidationError`. `TaskPlanner.replan()` dynamically updates queued plans when requirements evolve.

---

## 4. Structured Agent-to-Agent Handoffs (`orchestrator/handoff.py`)

Every task execution receives a bounded payload built by `HandoffManager.prepare_task_input()`:
- `blueprint` & `blueprint_analysis` (software category, FRs, NFRs, stack, excluded agents)
- `shared_context` (key architectural artifacts stored in `ProjectMemory`)
- `upstream_handoffs` (verified `HandoffEnvelope` records from every parent task in `task_dependencies`, including summary, structured metrics, and SHA-256 verified file paths)
- `acceptance_criteria` & `execution_history` (prior attempt errors when retrying)

After an agent finishes, `HandoffManager.verify_and_record()` confirms that every declared artifact actually exists inside the sandboxed project workspace, computes SHA-256 hashes, persists the envelope in `task_handoffs`, and updates `ProjectMemory`. If a task permanently fails after exhausting `max_retries`, `propagate_failure()` marks transitive downstream tasks `SKIPPED` so workers never deadlock.

---

## 5. SQLite Schema v3 (`memory/database.py`)

Core tables:
- `projects` — workspace path, status, lifecycle state, JSON metadata
- `tasks` & `task_dependencies` — multi-parent DAG, priority, retries, `acceptance_criteria`, `output_data`, worker lock timestamp (`locked_at`)
- `task_handoffs` — verified upstream-to-downstream `HandoffEnvelope` payloads and file digests
- `executions` & `execution_logs` — per-attempt start/end timestamps and redacted step logs
- `project_memory` — shared key-value JSON context per project
- `approvals` & `approval_audit_logs` — persistent TTL-bound human approvals and tamper-evident event trail
- `model_usage` — provider, model, prompt/completion tokens, latency, and estimated USD cost
- `artifacts` & `checkpoints` — SHA-256 deliverable registry and local Git commit SHAs

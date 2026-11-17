# Existing Architecture Audit (verified by code inspection, 2026-11-17)
Baseline: commit 94253fa, `python -m pytest tests/ -q` = **159 passed, 4 skipped** (live-only).
| Module | Actual status | Connected | Tests | Termux dep |
|---|---|---|---|---|
| agents/ (16 dirs) | Fully implemented: base.py 386L contracts, registry, per-agent prompts; real AI calls + offline fallback | Engine via registry | test_all_agents | No |
| orchestrator/engine 632L, planner 781L | Fully implemented: dynamic selection by blueprint type, cycle-checked multi-parent DAG, handoffs, retries, recovery | Yes | planner/orchestrator/handoff tests | No |
| orchestrator/scheduler, lifecycle, handoff | Implemented; single-worker default, stale-lock reclaim | Yes | yes | No |
| integrations/ai_providers | Gemini/OpenAI/Codex/Anthropic + router: retries, 429 backoff, fallback, cost telemetry | Yes | providers/router | No |
| memory/database (schema v3) | WAL SQLite, atomic claim, handoffs, approvals, checkpoints, artifacts | Yes | database/recovery | No |
| security/approval, secret_manager, validation | Single-use TTL approvals, audit log, redaction, ZIP traversal/ratio guards | Execution-layer gate | security/approvals | No |
| execution/ | command/file/sandbox/resource/document/packager — real local execution | Yes | yes | **Sandbox/command assume local FS** |
| integrations/github/client (227L) | **Partial**: local git checkpoints, secret scan, approval-gated push/pull, one REST call (repo_info). No branches/PRs/contents/compare APIs, single hardcoded default repo | Engine (one repo) | document_and_github (local) | git CLI |
| integrations/telegram (bot+handler 712L) | Fully implemented long-polling, inline approvals, dedup callbacks | Same engine | telegram/e2e | No |
| integrations/web | Setup portal for keys only — not a dashboard | No | web_setup | No |
| scripts/*.sh | Termux install/start/stop — optional after migration | — | — | **Yes** |
| Android app | **Absent** (blueprint classification only) | — | — | — |
Conflicts: (1) execution is local-workspace-centric vs GitHub workspace; (2) one global GitHub repo vs per-project repos — isolation risk; (3) no REST PR/branch layer; (4) no HTTP API for an Android client; (5) DB has no project↔repo/PR/artifact-binding tables. No feature is a fake placeholder; offline AI fallback is deterministic and labelled.

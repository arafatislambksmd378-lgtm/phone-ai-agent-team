# Crash Recovery, Persistence & Fault Tolerance

Android 14 aggressively reclaims background processes under memory pressure. `phone-ai-agent-team` is architected so that **a sudden `SIGKILL`, battery drain, or Termux restart never corrupts state or loses work**.

---

## 1. Durable SQLite WAL Persistence

- SQLite runs with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`, ensuring atomic commits across worker threads.
- Project lifecycle transitions (`LifecycleManager`), task statuses, retry counters, `task_handoffs`, `approvals`, and `checkpoints` are committed before and after every task step.

---

## 2. Automatic Startup & Stale-Lock Recovery

1. **Daemon Startup Recovery (`OrchestratorEngine.startup_recovery()`)**:
   - When `main.py` starts, `db.detect_interrupted()` locates any tasks left in `EXECUTING` from an earlier killed process, resets them cleanly to `QUEUED` (`db.recover_interrupted()`), transitions affected projects through `RECOVERING → QUEUED`, and restarts the on-demand worker pool.
2. **Stale Worker Lock Reclamation (`Database.claim_task(stale_after=...)`)**:
   - If a worker thread stalls longer than `STALE_TASK_SECONDS` (default 900s), `claim_task` automatically reclaims the stale lock without manual intervention.
3. **Subprocess Group Cleanup (`execution/process_manager.py`)**:
   - Child commands run in isolated process groups (`start_new_session=True`). On timeout or `SIGTERM`/`SIGINT`, `ProcessManager.terminate_all()` kills the entire process tree (`os.killpg`) so orphaned compilers or test runners never leak RAM.

---

## 3. Manual Recovery Commands

### Via Telegram
```text
/recover <project_id>
```
Requeues both interrupted (`EXECUTING`) and failed (`FAILED` + downstream `SKIPPED`) tasks, resets retry counters, and restarts execution from the last completed task in the DAG (already `COMPLETED` upstream tasks are not re-run).

### Via Termux CLI
```bash
# Requeue interrupted tasks and exit
python3 main.py --recover

# Inspect project states
python3 main.py --status
```

---

## 4. Local Git Checkpoints & Rollback

Every project workspace is initialized as an isolated Git repository (`execution/sandbox.py`). After each agent completes a task, `Sandbox.checkpoint_commit()` records a local commit SHA in the `checkpoints` table. To inspect or roll back a project workspace manually:

```bash
cd projects/<project_folder>
git log --oneline
git checkout <checkpoint_sha>
```

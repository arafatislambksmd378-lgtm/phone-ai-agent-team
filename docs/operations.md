# Operations manual

## Daily use

```sh
cd ~/phone-ai-agent-team && . .venv/bin/activate
python3 main.py              # bot + on-demand workers
```

| Task | Command |
|---|---|
| Check configuration | `python3 main.py --health` |
| Verify the install | `python3 main.py --selfcheck` |
| List projects | `python3 main.py --status` |
| Offline dry run | `python3 main.py --demo` / `--demo --quick` |
| Requeue after crash | `python3 main.py --recover` |
| Backup | `sh scripts/backup.sh` |
| Restore | `sh scripts/restore.sh backups/<file>` |

## Logs

- Console: timestamped, level-filtered (`LOG_LEVEL`).
- File: `logs/agent.log`, rotated at 1 MB, 2 backups kept (worst case ~3 MB).
- Database: every task execution writes to `execution_logs`, queryable with
  `/logs <project_id>`.

All three paths run through `security/secret_manager.redact()` before writing, so a key
that appears in a traceback is replaced by `[REDACTED_*]`.

## Recovery

Crash or `Ctrl+C` mid-task leaves the task in `EXECUTING`.
`python3 main.py --recover` (or `/recover <id>`) does three things:

1. marks dangling `executions` as `INTERRUPTED`,
2. returns `EXECUTING` tasks to `QUEUED`,
3. requeues `FAILED` tasks that still have retries left.

Nothing is deleted. Git checkpoints made before the crash remain in the project's
own repository (`git -C projects/<ws> log`).

## Backup and restore

```sh
sh scripts/backup.sh
# database -> backups/phone_agent_YYYYmmdd_HHMMSS.db
# projects -> backups/projects_YYYYmmdd_HHMMSS.tar.gz

sh scripts/restore.sh backups/phone_agent_20260101_120000.db
sh scripts/restore.sh backups/projects_20260101_120000.tar.gz
```

The backup script copies the live DB with SQLite's backup API, so it is safe to run
while the bot is working.

## Pause, cancel, approvals

- `/pause` finishes the current task, then idles the pool; `/resume` restarts it.
- `/stop <id>` marks remaining tasks `CANCELLED` and the project `CANCELLED`.
- Dangerous actions create an approval row first; `/approve <id>` or `/reject <id>`
  decides it exactly once. A project with a pending approval reports
  `WAITING_FOR_APPROVAL`, never `COMPLETED`.

## Benchmarking on the device (not yet measured)

The numbers in the table below were measured on Linux; repeat them on the phone.

```sh
# memory + cpu of a full pipeline (no AI keys => mock mode)
python3 - <<'PY'
import subprocess, sys, time, resource
t0 = time.time(); p = subprocess.run([sys.executable, "main.py", "--demo"])
u = resource.getrusage(resource.RUSAGE_CHILDREN)
print("exit", p.returncode, "wall %.1fs" % (time.time()-t0), "RSS %.1f MB" % (u.ru_maxrss/1024))
PY

# disk footprint
du -sh data projects logs backups
```

| Metric | Linux reference | Phone |
|---|---|---|
| Idle RSS (listener only) | 23 MB | *to measure* |
| Full pipeline peak RSS | 35 MB | *to measure* |
| Full pipeline wall time | 1.1 s (mock) | *to measure* |
| Disk per project | ~250 KB | *to measure* |
| Database after 1 project | 120 KB | *to measure* |

Expect AI mode to dominate wall time: each agent makes one network call, so a full
16-agent run with live providers is bounded by network latency, not CPU.

## Known Android limits

- Android may kill Termux when memory is low; use `termux-wake-lock` and disable battery
  optimisation. The DB is crash-safe, so recovery is a single command.
- `python3 -m venv` needs `python-venv` on some Termux builds — the installer handles it.
- Termux has no `systemd`; use `Termux:Boot` for autostart.

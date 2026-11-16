# Operations Manual

- Start: `python3 main.py` | Demo: `python3 main.py --demo` | Status: `python3 main.py --status`
- Recover after crash: `python3 main.py --recover` or Telegram `/recover <id>`
- Backup: `sh scripts/backup.sh` (DB + projects tarball)
- Restore: copy DB back to `data/phone_agent.db`, restart.
- Shutdown: Ctrl+C — scheduler stops, workers exit, Telegram listener stops. DB is crash-safe (WAL).
- Logs: `logs/` + DB `execution_logs` table. `/logs <project_id>` in Telegram.
- Troubleshooting: missing token → check `.env`; no AI keys → mock mode (deterministic scaffolds); port busy → change backend port.

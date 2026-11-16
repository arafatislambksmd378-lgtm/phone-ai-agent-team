#!/bin/sh
# Backup the database + generated projects into backups/ (Termux-safe).
set -eu
cd "$(dirname "$0")/.."

TS=$(date +%Y%m%d_%H%M%S)
mkdir -p backups

if [ -f data/phone_agent.db ]; then
    # Use SQLite's own backup so a live WAL database stays consistent.
    python3 - <<'PY'
import sqlite3, os, datetime
src = "data/phone_agent.db"
dst = "backups/phone_agent_%s.db" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
con = sqlite3.connect(src)
out = sqlite3.connect(dst)
with out:
    con.backup(out)
con.close(); out.close()
print("database ->", dst)
PY
else
    echo "no database yet (data/phone_agent.db missing)"
fi

if [ -d projects ] && [ -n "$(ls -A projects 2>/dev/null)" ]; then
    tar -czf "backups/projects_${TS}.tar.gz" projects
    echo "projects -> backups/projects_${TS}.tar.gz"
else
    echo "no projects to archive"
fi

echo "backup complete: $(ls -1 backups | tail -3 | tr '\n' ' ')"

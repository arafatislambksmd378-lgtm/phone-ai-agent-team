#!/bin/sh
# Restore database/projects from a backup produced by scripts/backup.sh
# Usage: sh scripts/restore.sh backups/phone_agent_20260101_120000.db
set -eu
cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "usage: $0 <backup.db|backup.tar.gz>" >&2
    echo "available backups:" >&2
    ls -1 backups 2>/dev/null >&2 || echo "  (none)" >&2
    exit 2
fi

SRC="$1"
[ -f "$SRC" ] || { echo "ERROR: $SRC not found" >&2; exit 1; }

case "$SRC" in
    *.db)
        mkdir -p data
        [ -f data/phone_agent.db ] && cp data/phone_agent.db "data/phone_agent.db.pre-restore.$(date +%s)"
        cp "$SRC" data/phone_agent.db
        echo "restored database from $SRC"
        ;;
    *.tar.gz)
        tar -xzf "$SRC"
        echo "restored projects from $SRC"
        ;;
    *)
        echo "ERROR: unknown backup type" >&2
        exit 1
        ;;
esac
echo "now run: python3 main.py --status"

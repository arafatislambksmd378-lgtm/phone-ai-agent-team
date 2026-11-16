#!/bin/sh
# Backup DB + projects (Termux)
set -e
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p backups
cp -v data/phone_agent.db "backups/phone_agent_${TS}.db" 2>/dev/null || echo "No DB yet"
tar -czf "backups/projects_${TS}.tar.gz" projects/ 2>/dev/null || echo "No projects yet"
echo "Backup done: backups/*_${TS}.*"

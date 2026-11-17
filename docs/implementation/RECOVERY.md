# Recovery Procedure
History is preserved remotely: commit 94253fa (pre-transformation baseline) and a7ab637 (audit
checkpoint) are ancestors of PR #3's branch, which is pushed to origin — no separate backup-branch
push was performed (Arena branch policy permits pushing only the session branch; creating/pushing
another branch was therefore not done). Recover with:
`git fetch origin && git checkout -b restore-baseline 94253fa` (original system) or
`git checkout -b restore-audit a7ab637` (audit checkpoint). DB: v4 migration is additive; restore any
SQLite backup file over DB_PATH while the backend is stopped. Never force-push to recover.

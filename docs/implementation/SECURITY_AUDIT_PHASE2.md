# Security Audit — Phase 2
Fixed/verified: API bearer token uses constant-time compare and is required on every route incl.
PUT/DELETE; GitHub token backend-only (Bearer header, never in URLs/logs — redact() on errors);
Android stores only the API token, Keystore AES-GCM encrypted; stale-SHA saves return conflict
instead of overwriting; merge consumes a single-use approval verified for project+action; repo
isolation re-checked (assert_repo) on every executor write; Telegram allowlist + duplicate-callback
protection unchanged. Remaining: deploy backend only behind HTTPS; rotate tokens if a device is lost
(revoke GitHub fine-grained token in GitHub settings).

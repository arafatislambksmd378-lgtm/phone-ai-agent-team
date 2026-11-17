# Phase 3 security and E2E report

## Scope and findings

- API handler and server reject missing/weak tokens, use `hmac.compare_digest`, limit failed attempts and bound JSON/upload sizes. Automated loopback tests exercise auth, rate limits and body validation; this is not penetration testing. TLS deployment must be configured by the operator.
- GitHub credentials stay in the backend; paths, refs, secret patterns, file SHAs and project bindings are checked. Secret scanning is heuristic, not proof of absence of secrets. Directory rename is not atomic: a failed delete after destination creation returns a partial-result error and requires manual repair.
- PR merge requires a project/repository/PR/head-SHA-scoped, approved, unexpired and single-use `pr_merge` approval. Consumption is atomic; the approved SHA is sent to GitHub's merge endpoint. Approval is consumed even if GitHub later rejects the merge, so request a new approval. These controls are mocked in tests, not live verified.
- ZIP checks reject traversal, symlinks and expansion bombs. Source and documents are untrusted. Code execution and pytest are **not OS-sandboxed**; GitHub publishing is blocked unless generated tests run and pass, and execution requires explicit operator opt-in in isolated infrastructure. The separate orchestration agent executor also needs isolation. Treat as a critical deployment blocker.
- Telegram authorization, sync, task deduplication and shared state have existing local/mock tests; no Telegram bot credential was available for a live check. In-memory job exclusion is per process, not distributed or durable; do not run multiple API processes sharing a database and assume exactly-once execution.
- The API has a shared bearer credential, not per-user RBAC or identity-bound approvals. A compromised token can exercise all API privileges. Failed-auth limiting is per process and direct peer address, not a substitute for proxy/network controls. Upload and secret checks cannot guarantee safety. Review all GitHub writes and use a least-privilege test repository.

## Live validation plan (not performed)

Use a new dedicated disposable GitHub repository and isolated VM/container with no credentials or sensitive files accessible to generated code, no broad network access, an unprivileged account and resource limits. Configure a narrowly scoped GitHub credential and a real AI provider key via the service's secure environment, not chat or shell history. Set a strong API token and start the service behind HTTPS. Only within the isolated environment, set `ALLOW_UNSANDBOXED_PROJECT_TESTS=1`; this switch is not itself isolation. The environment needs Python and pytest.

Create/select a project in Android, connect the dedicated repository, verify its actual default branch, and submit a small instruction that includes executable tests. Start GitHub execution; monitor tasks and DAG ordering, test output, branch and commit IDs, PR files and reviews. Independently inspect the remote PR and confirm tests ran and passed. Request merge authorization, approve it, and only merge if separately authorized. Verify SHA-pinning, single-use behavior and branch protection. Revoke the temporary credentials and clean up the disposable repository afterward. No live commit, PR, Telegram message, Android installation or merge was performed in this session.

This plan is not evidence that the full 10-step pipeline works: it has not been run end-to-end. Project test opt-in and isolation requirements are intentional blockers until an actual sandbox and a safe live run exist.

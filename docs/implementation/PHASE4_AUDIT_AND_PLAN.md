# Phase 4 inspection and implementation plan

Inspection baseline: `151fb5f` on `arena/eb81fa85-phone-ai-agent-team`, PR #4 open as draft. No visual reference attachments were supplied. The Phase 3 CI reported a successful build, not a working product or device test.

## Findings and root causes

1. **Navigation and presentation:** one programmatic `MainActivity` builds a horizontal row of nine default buttons, re-creates forms on every navigation and renders backend responses as raw JSON. No coherent design system, loading/empty states or dedicated project/agent/activity views. There is no real onboarding, notification center or Telegram setup screen.
2. **Dashboard:** returns immediately if no project is selected; does not fetch platform summary, repository count, agent or Telegram status. No clear onboarding or connection state. Asynchronous callbacks from different requests overwrite the single connection indicator.
3. **Editor data loss:** when the user confirms discard, `guard` closes the file before the rename action reads its path and SHA; rename then does nothing. A save that returns no SHA discards the editor, including changes typed during the request. Lifecycle recreation loses unsaved editor state.
4. **Connection persistence:** Phase 3 switched preferences from `secure` to `phone_agent_secure`, so users upgrading from the old app lose visible settings despite the key alias being retained. The storage class also silently treats decryption errors as an empty configuration.
5. **GitHub discovery:** users must type an owner/repository; there is no backend endpoint for authorized account/repository discovery. GitHub authentication is configured on the server, not through an Android OAuth flow. Repository-bound operations exist but are not live-tested end-to-end.
6. **Agent execution:** `execute_project_on_github` plans against a separate workspace; it does not retrieve the connected repository as context. Agents include deterministic fallbacks. It cannot honestly satisfy an instruction to analyze and repair an existing repository. It may overwrite files based on current remote SHA rather than a reviewed baseline. No verified live provider/test repository exists here.
7. **Critical execution isolation:** QA and debugger agents call pytest on generated workspace files; the workspace command agent can run interpreters and other programs. Scrubbing environment variables, allowlists, `shell=False`, timeouts and the class named `Sandbox` do not isolate filesystem, network or processes. The separate GitHub execution pytest opt-in is not OS isolation. Do not deploy with untrusted workloads; block unsafe paths until a verified isolated worker exists.
8. **Synchronization:** API and Telegram can share an engine in `--with-api` mode and persist tasks/approvals, but Telegram notification delivery, restart behavior, concurrent multi-process duplicate prevention and real cross-platform flows are unverified. A callback being attached is not proof that Telegram is connected.
9. **Security and networking:** bearer auth is fail-closed and Android uses HTTPS for remote hosts, but one shared bearer token has broad authority. GitHub and Telegram credentials are server-side; no per-user RBAC or secure credential-provisioning UX exists. Rate limiting is process-local. GitHub and Telegram live calls require dedicated credentials and external services.
10. **Testing:** existing Python suite and Android unit/build CI are useful but do not exercise the UI, network/device behavior, live bot or real AI changes. This environment has no local SDK, emulator, device, container runtime or live bot/model credentials. Artifact storage was not reachable in Phase 3. Emulator validation requires a suitable external CI runner.

## Plan

- Preserve the existing database, 16 agents, API contracts and branch. Make execution that lacks a trustworthy OS sandbox explicitly blocked rather than presenting it as safe; record the functionality impact.
- Implement an original restrained white/purple/lilac design, consistent touch targets and navigation. Connect dashboard metrics and status to real API responses, with clear loading, errors and empty states. Preserve existing repository/editor/PR workflows.
- Fix editor rename and async-save data-loss paths; migrate stored settings safely. Add a read-only, authenticated server-side GitHub repository listing if it can be implemented without exposing credentials.
- Add regression tests for every repaired backend/security path, and add Android UI/emulator tests when executable infrastructure is available. Build via CI, report the exact commit and artifact; never equate a successful build with a device test.
- Obtain live integration evidence only using dedicated test infrastructure and credentials configured out of band. Until then classify live AI/GitHub, Telegram and isolated execution as blocked or unverified. Do not merge PR #4.

### Additional hardening during implementation

Workspace file access now rejects `.git` metadata and checkpoint Git invocations disable Git hooks and global/system config. These controls are defense in depth, not an OS sandbox. The fail-closed execution policy deliberately causes existing QA/debugger/command-based end-to-end tests to be marked strict expected failures; those workflows must be restored only using a verified isolated worker.

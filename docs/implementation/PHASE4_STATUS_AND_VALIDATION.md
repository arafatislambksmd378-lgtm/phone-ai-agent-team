# Phase 4 status and validation

**Status: incomplete; not production-ready.** PR #4 remains a draft. This document records evidence and blockers, not an acceptance claim. See `PHASE4_AUDIT_AND_PLAN.md` for the initial root-cause audit. No visual-reference attachments were available.

## Design and repairs

The app previously used a horizontally scrolling row of default buttons, raw JSON and minimal feedback. The updated shared layout uses a soft-white canvas, purple active navigation and actions, white bordered cards, green response status, a restrained yellow execution warning, and lilac action surfaces. It adds a five-item bottom navigation with a More menu. Existing screens use shared typography, spacing, inputs and actions. The overview uses real project, task, approval, agent and health API responses rather than demo values. Repository discovery uses a server-side GitHub credential; account identity and accessible repositories can be queried without sending the credential to the app. A Telegram information screen and project activity screen make clear that an attached callback is not proof of Telegram connectivity.

The editor rename action now retains the path and SHA after a discard confirmation; a save response without a SHA no longer discards editor contents. Editor text and SHA are included in Android instance state. Existing Phase 2 Keystore preferences can be read and migrated. API clients now treat `ok:false` as failure even with HTTP 200. GitHub redirects and pagination links cannot forward credentials to a lookalike host. These changes have not all been validated interactively on a device.

## Feature classification

| Feature | Status | Evidence / remaining work |
|---|---|---|
| Shared visual style, home, projects, settings and navigation | PARTIALLY IMPLEMENTED | Existing screens restyled; Android 14 emulator smoke test on `fa92dcd` launched, navigated to Settings, Projects and Telegram, and recreated the activity. Not every screen or control was exercised. |
| Dashboard metrics and activity | PARTIALLY IMPLEMENTED | Uses backend API counts and sync events. Network/device behavior and complete activity presentation are not verified. |
| Account / repository discovery | PARTIALLY IMPLEMENTED | Real read-only GitHub account, accessible-repositories and public repository metadata queries returned HTTP 200 via `GitHubAPI` with the sandbox's configured credential. Local HTTP endpoint tests use a mocked GitHub transport. Android-to-live-backend discovery is not verified. |
| File browse/editor, Git branches, PRs and approvals | PARTIALLY IMPLEMENTED | Existing endpoint logic retained, file-editor bugs repaired; automated coverage is predominantly local/mocked. No live mutation, merge or complete device workflow was performed. Renames are not atomic. |
| 16 agents, dynamic planner and DAG | PARTIALLY IMPLEMENTED | Registry and orchestration tests remain, but QA/test and workspace-command stages are blocked without isolation; end-to-end completion is not currently available. Deterministic agent fallbacks exist and are not proof of real AI work. |
| Repository-aware AI execution | BLOCKED | Repository snapshot/context is not loaded by the existing pipeline; no verified live provider or dedicated authorized test repo. GitHub agent execution now refuses before planning or publishing. |
| OS isolation of generated code | BLOCKED | No independently verified OS-isolated worker is integrated. QA/debugger pytest, workspace commands and GitHub agent execution are fail-closed; the old environment-variable opt-in no longer enables host testing. This is an intentional regression until a real worker is implemented. Other service code, parsers and dependencies have not had an independent sandbox/security audit. |
| Telegram bot and bidirectional sync | NOT VERIFIED | The existing shared-engine architecture and mocked tests remain. No live bot credentials or live cross-platform delivery tests were available. Callback attachment is not connectivity verification. In-memory duplicate guards do not provide distributed exactly-once semantics. |
| Android emulator | PARTIALLY IMPLEMENTED | [Android 14 emulator run](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44048308769) passed one navigation and recreation smoke test on commit `fa92dcd`, not the requested comprehensive device matrix. No physical Android device was available. |
| Security / authentication | PARTIALLY IMPLEMENTED | Existing fail-closed bearer auth, approval and request-boundary tests; additional safety and redirect tests. No independent penetration test, per-user RBAC, or verified isolated execution infrastructure. |

## Tests and interpretation

Local Python: `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/ -q -o addopts=''` returned **181 passed, 4 skipped, 8 xfailed** after the latest Python changes. The four skips are optional live integrations. The eight strict expected failures retain legacy assertions requiring previously unrestricted host execution; they are deliberately not counted as passes. Their names and reason are in `tests/conftest.py`. Re-enable and revalidate them only after a real isolated worker is integrated. Android unit tests and APK build passed on commit `fa92dcd`; one Android 14 emulator navigation/recreation test passed on that commit. Neither test suite verifies a live backend.

## Deployment and device checklist

Use a remote backend protected by HTTPS, a strong API bearer token and least-privilege server-side GitHub credentials; follow `PHASE3_BACKEND_DEPLOYMENT.md` with its Phase 4 superseding note. Never deploy it as a safe untrusted-code execution service until isolation is implemented and independently verified. Install the matching commit's debug APK from the `Android APK` GitHub Actions artifact (a debug build is not a signed production release). On Android 14: launch, configure HTTPS and token, restart, create/select a test project, connect a dedicated repo, browse/edit and test SHA conflicts, inspect branches and PRs, and check approvals and offline recovery. Validate Telegram and live AI only with a dedicated bot, test repository, controlled provider credentials and an independently isolated worker; none of those live operations was verified here. Do not enter credentials into chat.

## Debug APK evidence

[Download `app-debug-apk` from Android APK workflow run 44048546568](https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team/actions/runs/44048546568), push build for commit `d6260c8e07cd58bf44c62fe2e9e094daa6fb3db0`. Version `4.0.0-preview`, APK size **5,663,703 bytes**, APK SHA-256 **8e795a5013af70c8cf1630e821a8cead95a570737913929c77600e4ffa4575fd**, emitted by CI. The sandbox cannot download GitHub's external artifact storage, so the APK hash and size are CI-reported and not independently checked here. The emulator workflow installed a separately built debug APK, not this downloaded artifact.

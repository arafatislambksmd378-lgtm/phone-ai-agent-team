# 16-Agent Specialist Guide

All 16 agents inherit from `BaseAgent` (`agents/base.py`), execute **on demand** inside the single worker pool (never as 16 background processes), declare explicit `AgentContract` schemas, log every phase to SQLite, and validate outputs against completion criteria before returning `AgentResult`.

---

## Agent Roster & Contracts

### 1. Chief AI Orchestrator (`chief_orchestrator`)
- **Responsibility**: Blueprint synthesis (`mode=intake`) or final executive delivery sign-off (`mode=final_gate`).
- **Tools**: `read_file`, `write_file`, `list_files`, `ai_complete`.
- **Outputs**: `docs/EXECUTIVE_SUMMARY.md` summarizing delivered capabilities, architecture, QA/security status, and excluded-agent rationale.

### 2. Requirement Analyst (`requirement_analyst`)
- **Responsibility**: Parses untrusted blueprint text & extracted documents into structured Functional Requirements (`FR-xx`), Non-Functional Requirements (`NFR-xx`), user stories, data entities, API endpoints, CLI commands, and edge cases.
- **Outputs**: `spec/requirements_spec.json`, `docs/REQUIREMENTS.md`, and `ProjectMemory["spec"]`.

### 3. Software Architect (`software_architect`)
- **Responsibility**: Selects a Termux/Android-appropriate stack and directory layout tailored to `software_type` (`web_app`, `rest_api`, `cli_tool`, `python_library`, `database_app`, `android_app`, `desktop_app`).
- **Outputs**: `spec/architecture.json`, `docs/ARCHITECTURE.md`, and `ProjectMemory["architecture"]`.

### 4. UI/UX Designer (`uiux_designer`)
- **Responsibility**: Produces responsive mobile-first design tokens (`styles/tokens.css`), accessible HTML layout wireframes, or Android XML / Desktop Tkinter layout specs. Automatically skipped for headless `rest_api`, `cli_tool`, `python_library`, and `database_app` blueprints.
- **Outputs**: `styles/tokens.css` (web), `spec/ui_design.json`, `docs/UI_UX_SPEC.md`.

### 5. Frontend Developer (`frontend_developer`)
- **Responsibility**: Builds XSS-safe (`textContent` DOM rendering, zero `innerHTML` injection), accessible, mobile-responsive HTML/CSS/JS interfaces wired to the backend API endpoints defined in `spec/api_contract.json` or `spec/architecture.json`. Skipped for headless projects.
- **Outputs**: `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`.

### 6. Backend Developer (`backend_developer`)
- **Responsibility**: Multi-workflow core software engineer. Uses live AI multi-file generation (`parse_files_block` + `ast.parse` syntax verification) when configured, or clean category-specific stdlib implementations across:
  - `web_app` / `rest_api` / `backend_service` (`backend/server.py`, `backend/app.py`, `backend/storage.py`)
  - `cli_tool` (`cli/main.py`, `cli/core.py` with `argparse` + JSON/SQLite state)
  - `python_library` (`src/<pkg>/__init__.py`, `src/<pkg>/core.py`, `pyproject.toml`)
  - `database_app` (`db_app/repository.py`, `db_app/cli.py`)
  - `android_app` (`android/app/build.gradle.kts`, `AndroidManifest.xml`, `MainActivity.kt`, `DomainLogic.kt` + Python domain verification twin)
  - `desktop_app` (`desktop/core.py` headless controller + `desktop/gui_tk.py` Tkinter UI)

### 7. Database Engineer (`database_engineer`)
- **Responsibility**: Designs normalized SQLite WAL schemas, indexes, foreign keys, and idempotent migration runners derived from blueprint entities.
- **Outputs**: `database/schema.sql`, `database/migrations.py`, `docs/DATABASE.md`.

### 8. API & Integration Agent (`api_integrator`)
- **Responsibility**: Generates OpenAPI 3.1 specifications (`spec/openapi.json`), typed stdlib Python client SDKs (`integrations/api_client.py`), and endpoint contract documentation (`docs/API_REFERENCE.md`) verified against backend routes.

### 9. Security Engineer (`security_engineer`)
- **Responsibility**: Performs real static security analysis across all workspace files (hardcoded secrets via `SecretManager`, `eval`/`exec`, `shell=True`, SQL string concatenation, `innerHTML` XSS sinks, insecure HTTP URLs) plus AI threat modeling.
- **Outputs**: `docs/SECURITY_AUDIT.md`, `spec/security_findings.json`.

### 10. QA & Testing Agent (`qa_tester`)
- **Responsibility**: Generates category-aware `pytest` test suites (`tests/test_generated_project.py`) tailored to the actual produced modules (HTTP server tests, CLI `main()` invocation tests, library API tests, repository CRUD tests, headless desktop/Android domain tests) and **executes `pytest` via `CommandRunner`**, reporting real exit codes and stdout/stderr — never fabricating pass results.

### 11. Debugging & Code Repair Agent (`debugger`)
- **Responsibility**: Inspects `qa_report` and test output. Fast-paths if all tests already pass; otherwise executes up to 3 bounded diagnosis-and-repair cycles (inspecting failing tracebacks + source files, applying syntax-checked repairs, and re-running `pytest` to verify regression-free resolution).

### 12. Code Review & Optimization Agent (`code_reviewer`)
- **Responsibility**: Computes real AST metrics (line counts, function lengths, bare `except:`, mutable default args, missing docstrings, Android 3 GB RAM footprint hazards) combined with AI qualitative review.
- **Outputs**: `docs/CODE_REVIEW.md`, `spec/code_review.json`.

### 13. DevOps & Deployment Agent (`devops`)
- **Responsibility**: Detects actual host/Termux toolchains (`python3`, `git`, `gradle`, `sdkmanager`), generates runnable `scripts/run.sh`, `scripts/healthcheck.sh`, `.env.example`, and CI workflows (`.github/workflows/ci.yml`), and enforces `ApprovalManager` before any live `production_deploy`.

### 14. Technical Documentation Agent (`documentation`)
- **Responsibility**: Synthesizes project-specific `README.md` and `docs/USER_GUIDE.md` grounded in actual workspace files, commands, endpoints, and test status.

### 15. Release Manager (`release_manager`)
- **Responsibility**: Verifies category-matched deliverable completeness, compiles `CHANGELOG.md` and `RELEASE_NOTES.md` with SHA-256 file manifests, and enforces `ApprovalManager` before any `public_github_release` or `github_push_public`.

### 16. Workspace & Tool Execution Agent (`workspace_executor`)
- **Responsibility**: Sandboxed file and allowlisted command runner wired directly to `ApprovalManager` and `ProcessManager` with strict path-escape prevention and redacted output capture.

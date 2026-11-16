"""Agent 13 — DevOps & Deployment Agent: category-aware Termux scripts, toolchain checks & approval gate."""
from __future__ import annotations

import shutil
import stat
from pathlib import Path

from agents.base import AgentResult, BaseAgent
from agents.devops.prompts import SYSTEM_PROMPT, build_user_prompt
from agents.helpers import title_from


class Agent(BaseAgent):
    agent_id = "devops"
    name = "DevOps & Deployment Agent"
    description = "Creates Termux-safe run scripts, environment guides, and deployment configs with approval enforcement."
    responsibilities = [
        "Generate POSIX shell scripts tailored to the project category (HTTP server, CLI, Desktop, Android)",
        "Detect local Termux/host build toolchains honestly without faking unsupported builds",
        "Enforce explicit human approval before any production deployment or external build trigger",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["scripts/run.sh", "docs/deployment.md", "docs/env.md"],
        "required_data_keys": ["software_type", "executable", "toolchains"],
    }
    completion_criteria = ["scripts/run.sh (executable), docs/deployment.md, and docs/env.md created"]

    def execute(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        ctx = self.build_context(task, extra_files=["docs/requirements.json"])
        project_id = ctx["project_id"]

        # Production deployment gate: strictly require verified human approval when requested
        if payload.get("deploy_production") and self.approval_manager is not None:
            approval_id = payload.get("approval_id")
            ok_appr, msg = self.approval_manager.verify(
                approval_id, project_id=project_id or None, action="production_deploy", actor="devops"
            )
            if not ok_appr:
                req_id = self.approval_manager.request(
                    project_id, "production_deploy", "Production deployment requested by DevOps task",
                    task_id=task.get("id"),
                )
                return AgentResult(
                    ok=False,
                    summary=f"Production deployment blocked pending human approval ({req_id})",
                    data={"needs_approval": True, "approval_id": req_id, "software_type": ctx["software_type"],
                          "executable": False, "toolchains": {}},
                    error=f"Requires human approval ({msg})",
                    mode="deterministic",
                )

        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        software_type = ctx["software_type"]
        toolchains = {
            t: shutil.which(t) is not None
            for t in ("python3", "pytest", "git", "gradle", "kotlinc", "aapt2")
        }

        run_sh, entrypoint, extra_files = self._build_run_script(software_type, toolchains)
        self._write("scripts/run.sh", run_sh)
        chmod_ok = self._chmod_exec("scripts/run.sh")

        files = ["scripts/run.sh", "docs/deployment.md", "docs/env.md", *extra_files]

        fallback_deploy = self._fallback_deploy(title, software_type, entrypoint, toolchains, ctx["platform_limitations"])
        prompt = build_user_prompt(
            title=title,
            software_type=software_type,
            entrypoint=entrypoint,
            toolchains=toolchains,
            limitations=ctx["platform_limitations"],
        )
        gen = self.generate(prompt, fallback_deploy)
        self._write("docs/deployment.md", gen.text if "#" in gen.text else fallback_deploy)

        env_doc = (
            "# Environment Configuration\n\n"
            "| Variable | Purpose | Default |\n|---|---|---|\n"
            "| `PORT` | HTTP server port (web/API projects) | `8000` |\n"
            "| `APP_DB` / `CLI_DB` | SQLite database file path | `app.db` |\n\n"
            "Never commit real API keys or tokens; store secrets only in `.env` (`chmod 600`).\n"
        )
        self._write("docs/env.md", env_doc)

        return AgentResult(
            ok=True,
            summary=f"Deployment artifacts written for {software_type} ({gen.mode}, exec={chmod_ok})",
            files=files,
            data={
                "software_type": software_type,
                "entrypoint": entrypoint,
                "executable": chmod_ok,
                "toolchains": toolchains,
            },
            mode=gen.mode,
            error=gen.error,
        )

    def _build_run_script(self, software_type: str, toolchains: dict[str, bool]) -> tuple[str, str, list[str]]:
        extra: list[str] = []
        if software_type == "cli_tool":
            sh = (
                "#!/bin/sh\n"
                "# Run generated CLI utility (Termux-compatible).\n"
                "set -eu\ncd \"$(dirname \"$0\")/..\"\n"
                "exec python3 cli/main.py \"$@\"\n"
            )
            return sh, "cli/main.py", extra

        if software_type == "python_library":
            sh = (
                "#!/bin/sh\n"
                "# Verify generated Python library via pytest.\n"
                "set -eu\ncd \"$(dirname \"$0\")/..\"\n"
                "PYTHONPATH=src exec python3 -m pytest -q\n"
            )
            return sh, "src/", extra

        if software_type == "android_app":
            ci_yml = (
                "name: Android Debug APK (Manual Approval Only)\n"
                "on:\n  workflow_dispatch:\n"
                "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-java@v4\n        with:\n"
                "          distribution: temurin\n          java-version: '17'\n"
                "      - name: Run domain & manifest verification\n"
                "        run: python3 -m pytest -q\n"
            )
            self._write(".github/workflows/android-build.yml", ci_yml)
            extra.append(".github/workflows/android-build.yml")
            sh = (
                "#!/bin/sh\n"
                "# Verify Android project locally; use Gradle only if Android SDK is installed.\n"
                "set -eu\ncd \"$(dirname \"$0\")/..\"\n"
                "if command -v gradle >/dev/null 2>&1 && [ -n \"${ANDROID_HOME:-}\" ]; then\n"
                "    echo \"Android SDK detected — building debug APK...\"\n"
                "    exec gradle assembleDebug --no-daemon\n"
                "else\n"
                "    echo \"NOTE: Android SDK / Gradle not installed in this Termux environment (3 GB RAM safe mode).\"\n"
                "    echo \"Running portable domain & manifest verification suite instead:\"\n"
                "    exec python3 -m pytest -q\n"
                "fi\n"
            )
            return sh, "app/src/main/java/com/phoneagent/app/MainActivity.kt", extra

        if software_type == "desktop_app":
            sh = (
                "#!/bin/sh\n"
                "# Launch Tkinter desktop app (falls back to --headless-check without DISPLAY).\n"
                "set -eu\ncd \"$(dirname \"$0\")/..\"\n"
                "exec python3 desktop/app.py \"$@\"\n"
            )
            return sh, "desktop/app.py", extra

        sh = (
            "#!/bin/sh\n"
            "# Start generated HTTP/SQLite application (Termux-safe, no root, no Docker).\n"
            "set -eu\ncd \"$(dirname \"$0\")/..\"\n"
            "export PORT=\"${PORT:-8000}\"\n"
            "echo \"starting server on port $PORT\"\n"
            "exec python3 backend/app.py\n"
        )
        return sh, "backend/app.py", extra

    @staticmethod
    def _fallback_deploy(title: str, software_type: str, entrypoint: str,
                         toolchains: dict[str, bool], limitations: list[str]) -> str:
        lims = "\n".join(f"- {x}" for x in limitations) or "- Runs natively in Termux with Python 3.10+ stdlib."
        return f"""# Deployment Guide — {title}

## Target Environment
- **Category:** `{software_type}`
- **Entrypoint:** `{entrypoint}`
- **Runtime:** Android 14 / Termux (3 GB RAM, no Docker, no Redis, no PostgreSQL)

## Run
```sh
sh scripts/run.sh
```

## Persistence & Backups
- SQLite databases operate in WAL mode; back up cleanly via `sqlite3 app.db ".backup app.db.bak"`.

## Platform & Toolchain Notes
{lims}
- Detected host tools at generation time: `{', '.join(k for k, v in toolchains.items() if v) or 'python3'}`
"""

    def _chmod_exec(self, rel: str) -> bool:
        try:
            p = Path(self.workspace) / rel
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return True
        except Exception:
            return False

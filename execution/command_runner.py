"""Command execution — allowlist, no shell, timeouts, output caps, execution-enforced approval gate."""
from __future__ import annotations
import shlex
import subprocess
import time
from pathlib import Path

from config.permissions import is_command_allowed
from execution.process_manager import ProcessManager
from security.secret_manager import redact

MAX_OUTPUT = 8000


class CommandRunner:
    def __init__(self, workspace: str, timeout: int = 120,
                 approval_manager=None, project_id: str = "",
                 process_manager: ProcessManager | None = None):
        self.workspace = str(Path(workspace).resolve())
        self.timeout = max(1, int(timeout))
        self.approval_manager = approval_manager
        self.project_id = project_id
        self.process_manager = process_manager

    def run(self, command: str, approved: bool = False,
            approval_id: str | None = None) -> dict:
        allowed, needs_approval = is_command_allowed(command)
        if not allowed:
            return {"ok": False, "error": "Command blocked by policy", "stdout": "", "stderr": "",
                    "blocked": True}
        if needs_approval:
            if self.approval_manager is not None:
                # Execution-layer enforcement: never allow bypassing ApprovalManager with approved=True
                ok_appr, msg = self.approval_manager.verify(
                    approval_id,
                    project_id=self.project_id or None,
                    actor="command_runner",
                )
                if not ok_appr:
                    req_id = ""
                    if not approval_id and self.project_id:
                        req_id = self.approval_manager.request(
                            self.project_id, "destructive_command", f"Command: {command[:300]}"
                        )
                    return {
                        "ok": False,
                        "error": f"Requires verified human approval ({msg})",
                        "stdout": "",
                        "stderr": "",
                        "needs_approval": True,
                        "approval_id": req_id or (approval_id or ""),
                    }
            elif not approved:
                return {"ok": False, "error": "Requires human approval", "stdout": "", "stderr": "",
                        "needs_approval": True}
        # A command allowlist and an approval do not isolate untrusted code or files.
        # Fail closed until this call is delegated to a verified isolated worker.
        from execution.isolation import isolated_worker_available, BLOCKED_MESSAGE
        if not isolated_worker_available():
            return {"ok": False, "blocked": True, "stdout": "", "stderr": "",
                    "error": BLOCKED_MESSAGE}
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return {"ok": False, "error": f"Parse error: {e}", "stdout": "", "stderr": ""}
        if not parts:
            return {"ok": False, "error": "Empty command", "stdout": "", "stderr": ""}
        try:
            if self.process_manager is not None:
                raw = self.process_manager.run_managed(
                    f"cmd_{time.monotonic_ns()}", parts, cwd=self.workspace,
                    timeout=self.timeout, env=_safe_env(),
                )
                if "returncode" in raw:
                    return {
                        "ok": raw["ok"],
                        "returncode": raw["returncode"],
                        "stdout": redact(raw.get("stdout", "")[-MAX_OUTPUT:]),
                        "stderr": redact(raw.get("stderr", "")[-MAX_OUTPUT:]),
                    }
                return {"ok": False, "error": redact(raw.get("error", "Command failed")),
                        "stdout": "", "stderr": ""}
            proc = subprocess.run(parts, cwd=self.workspace, capture_output=True, text=True,
                                  timeout=self.timeout, env=_safe_env())
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "stdout": redact(proc.stdout[-MAX_OUTPUT:]),
                    "stderr": redact(proc.stderr[-MAX_OUTPUT:])}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Timeout after {self.timeout}s", "stdout": "", "stderr": ""}
        except FileNotFoundError:
            return {"ok": False, "error": f"Program not found: {parts[0]}", "stdout": "", "stderr": ""}
        except Exception as e:
            return {"ok": False, "error": redact(str(e)), "stdout": "", "stderr": ""}


def _safe_env() -> dict:
    """Minimal environment: never hand provider keys to generated code."""
    import os
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PWD", "TERM", "ANDROID_DATA",
            "ANDROID_ROOT", "PREFIX")
    return {k: v for k, v in os.environ.items() if k in keep}

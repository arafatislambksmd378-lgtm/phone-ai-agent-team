"""Command execution — allowlist, no shell, timeouts, output caps, approval gate."""
from __future__ import annotations
import shlex
import subprocess
from pathlib import Path

from config.permissions import is_command_allowed
from security.secret_manager import redact

MAX_OUTPUT = 8000


class CommandRunner:
    def __init__(self, workspace: str, timeout: int = 120):
        self.workspace = str(Path(workspace).resolve())
        self.timeout = max(1, int(timeout))

    def run(self, command: str, approved: bool = False) -> dict:
        allowed, needs_approval = is_command_allowed(command)
        if not allowed:
            return {"ok": False, "error": "Command blocked by policy", "stdout": "", "stderr": "",
                    "blocked": True}
        if needs_approval and not approved:
            return {"ok": False, "error": "Requires human approval", "stdout": "", "stderr": "",
                    "needs_approval": True}
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return {"ok": False, "error": f"Parse error: {e}", "stdout": "", "stderr": ""}
        if not parts:
            return {"ok": False, "error": "Empty command", "stdout": "", "stderr": ""}
        try:
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

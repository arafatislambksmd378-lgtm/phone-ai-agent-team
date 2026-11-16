"""Command execution with allowlist, timeouts, approval gates. No shell=True."""
from __future__ import annotations
import shlex
import subprocess
from config.permissions import is_command_allowed

class CommandRunner:
    def __init__(self, workspace: str, timeout: int = 120):
        self.workspace = workspace
        self.timeout = timeout

    def run(self, command: str, approved: bool = False) -> dict:
        allowed, needs_approval = is_command_allowed(command)
        if not allowed:
            return {"ok": False, "error": "Command blocked by policy", "stdout": "", "stderr": ""}
        if needs_approval and not approved:
            return {"ok": False, "error": "Requires human approval", "stdout": "", "stderr": "", "needs_approval": True}
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return {"ok": False, "error": f"Parse error: {e}", "stdout": "", "stderr": ""}
        try:
            proc = subprocess.run(parts, cwd=self.workspace, capture_output=True, text=True, timeout=self.timeout)
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timeout", "stdout": "", "stderr": ""}
        except FileNotFoundError:
            return {"ok": False, "error": "Program not found", "stdout": "", "stderr": ""}
        except Exception as e:
            return {"ok": False, "error": str(e), "stdout": "", "stderr": ""}

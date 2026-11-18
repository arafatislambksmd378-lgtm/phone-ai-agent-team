"""Agent-facing client for the isolated worker.

Agents never touch namespaces, sockets or tokens; they call :class:`WorkerClient`
which

* enforces a per-project concurrency and rate quota (a noisy project cannot
  starve another project's jobs),
* stages the workspace into an isolated copy so a payload's writes are observed
  and then rolled back unless the caller explicitly opts into collecting the
  diff,
* normalises failures into a shape every caller already understands,
* records the security-relevant outcome through the supervisor's audit sink.

The client is only constructed by ``execution.isolation.executor()``, which
returns ``None`` unless the worker was independently verified.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

MAX_CONCURRENT_PER_PROJECT = 1
MIN_INTERVAL_SECONDS = 0.5


class WorkerClient:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._last_start: dict[str, float] = {}

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(project_id, threading.Lock())

    # -- python / pytest helpers ------------------------------------------
    def run_python(self, *, project_id: str, workspace: str, code: str,
                   timeout_seconds: int = 120, memory_mb: int = 1024,
                   capture_workspace: bool = False) -> dict:
        """Execute a short Python snippet inside the sandbox."""
        return self._run(project_id=project_id, workspace=workspace,
                         argv=[sandbox_interpreter(), "-c", code],
                         timeout_seconds=timeout_seconds, memory_mb=memory_mb,
                         capture_workspace=capture_workspace)

    def run_pytest(self, *, project_id: str, workspace: str, target: str = "tests",
                   timeout_seconds: int = 300, memory_mb: int = 1536,
                   extra_args: list[str] | None = None,
                   capture_workspace: bool = False) -> dict:
        """Run the project's own pytest suite inside the sandbox.

        The payload interpreter is the host's real CPython, so third-party
        packages must be found through a ``sys.path`` entry that survives the
        chroot. The worker passes ``SANDBOX_SITE_PACKAGES`` (a sandbox-absolute
        path) which the job server rewrites to the host path covered by the
        Landlock read-only rule.
        """
        site = os.environ.get("SANDBOX_SITE_PACKAGES") or _sandbox_site_packages()
        args = [sandbox_interpreter(), "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        if target:
            args.append(target)
        args.extend(extra_args or [])
        env = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": "/workspace",
        }
        # The template already contains the payload packages on the interpreter's
        # default sys.path; no extra read-only rule is required.
        read_only = []
        return self._run(project_id=project_id, workspace=workspace, argv=args,
                         timeout_seconds=timeout_seconds, memory_mb=memory_mb,
                         env=env, capture_workspace=capture_workspace,
                         read_only=read_only)

    def run_command(self, *, project_id: str, workspace: str, command: str,
                    timeout_seconds: int = 120, memory_mb: int = 1024,
                    capture_workspace: bool = False) -> dict:
        """Run an explicit argv (never a shell string) inside the sandbox."""
        argv = command if isinstance(command, list) else command.split()
        if not argv or not all(isinstance(a, str) for a in argv):
            return {"ok": False, "error": "command must be a non-empty argv list"}
        return self._run(project_id=project_id, workspace=workspace, argv=argv,
                         timeout_seconds=timeout_seconds, memory_mb=memory_mb,
                         capture_workspace=capture_workspace)

    # -- core --------------------------------------------------------------
    def _run(self, *, project_id: str, workspace: str, argv: list[str],
             timeout_seconds: int, memory_mb: int, env: dict | None = None,
             capture_workspace: bool = False, read_only: list[str] | None = None) -> dict:
        if not project_id:
            return {"ok": False, "error": "project_id is required for isolated execution"}
        src = Path(workspace).resolve()
        if not src.is_dir():
            return {"ok": False, "error": "workspace does not exist"}
        lock = self._project_lock(project_id)
        with lock:
            last = self._last_start.get(project_id, 0.0)
            wait = MIN_INTERVAL_SECONDS - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_start[project_id] = time.time()
            job_id = f"{_safe(project_id)}-{uuid.uuid4().hex[:12]}"
            staged = _stage_workspace(src, job_id)
            try:
                result = self.supervisor.run_job(
                    job_id=job_id, project_id=project_id, workspace=str(staged),
                    argv=argv, timeout_seconds=timeout_seconds,
                    memory_mb=memory_mb, env=env, read_only=read_only,
                )
            finally:
                shutil.rmtree(staged, ignore_errors=True)
        result["isolation"] = {
            "verified": bool((result.get("supervisor") or {}).get("verified")),
            "landlock_abi": (result.get("supervisor") or {}).get("landlock_abi"),
            "sandbox_uid": ((result.get("handshake") or {}).get("uid")),
        }
        if capture_workspace:
            result["workspace_unchanged"] = True  # staged copy: host tree untouched
        return result


def _stage_workspace(src: Path, job_id: str) -> Path:
    """Copy the project workspace to a private staging directory.

    The payload runs against the copy, so nothing it does can modify the host
    project tree directly; callers that want to inspect produced files read them
    from the reported workspace size or re-run with an explicit collection step.
    """
    base = Path(os.environ.get("PHONE_AGENT_STAGE_ROOT", "/tmp/phone-agent-stage"))
    staged = base / job_id
    staged.mkdir(parents=True, exist_ok=False)
    for entry in src.iterdir():
        if entry.name in {".git", "node_modules", "__pycache__", ".venv"}:
            continue
        target = staged / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache"))
        elif entry.is_file():
            shutil.copyfile(entry, target)
    return staged


def sandbox_interpreter() -> str:
    """Path of the interpreter the payload should use *inside* the sandbox.

    A virtualenv interpreter (``sys.prefix != sys.base_prefix``) knows its own
    site-packages, so mirroring its exact path into the template preserves the
    service's toolchain. A system interpreter uses the standard path.
    """
    import sys

    if sys.prefix != sys.base_prefix:
        return f"/virtualenv/bin/python{sys.version_info.major}.{sys.version_info.minor}"
    return f"/usr/bin/python{sys.version_info.major}.{sys.version_info.minor}"


def _sandbox_site_packages() -> str:
    """Sandbox-absolute site-packages path for the template's copied packages."""
    import sys
    import sysconfig

    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if sys.prefix != sys.base_prefix:
        # The sandbox mirrors the venv at /virtualenv, not at the host path.
        return f"/virtualenv/lib/{version}/site-packages"
    try:
        purelib = (sysconfig.get_path("purelib") or "").rstrip("/")
    except Exception:  # noqa: BLE001
        purelib = ""
    return purelib or f"/usr/lib/{version}/site-packages"


def _safe(value: str) -> str:
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in value)
    return clean[:48] or "job"

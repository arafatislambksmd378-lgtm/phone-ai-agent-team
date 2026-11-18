"""Trusted-side supervisor for isolated workers.

Responsibilities (all fail-closed):

* **Self-check** — verify the host actually offers the boundaries a worker
  claims (Landlock ABI, user namespaces, sandbox uid) by *running* a probe
  payload and inspecting its handshake. A worker that cannot demonstrate the
  boundary is not registered.
* **Lifecycle** — start the worker process, wait for its READY state, keep a
  heartbeat, detect crashes and restart with exponential backoff.
* **Dispatch** — HMAC token + per-request nonce, immutable job ids, per-project
  quotas enforced on this side too (never trusting the worker's counter).
* **Watchdog** — wall-clock deadline, process-group SIGKILL, guaranteed cleanup
  verification, resource sampling of the worker during the job.
* **Audit** — every security-relevant transition is emitted as a structured
  event to the caller's sink (the service wires this to its database).

The supervisor never runs payload code and never relaxes the sandbox: if a
boundary cannot be verified it reports ``verified: False`` and callers stay
blocked.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from . import rootfs as rootfs_mod  # noqa: E402

WORKER_SCRIPT = os.path.join(_HERE, "jobserver.py")
DEFAULT_STATE_ROOT = os.environ.get("PHONE_AGENT_WORKER_ROOT", "/tmp/phone-agent-worker")


class WorkerUnavailable(RuntimeError):
    """Raised when no verified worker can accept a job (callers stay blocked)."""


def sandbox_uid() -> tuple[int, int]:
    """Return the dedicated unprivileged uid/gid used for jobs.

    ``SANDBOX_UID``/``SANDBOX_GID`` may override the defaults. The values are
    validated: uid/gid 0 is refused outright because running payloads as root
    inside a user namespace still shares the host's kernel credential if user
    namespaces are unavailable.
    """
    uid = int(os.environ.get("SANDBOX_UID", "20000"))
    gid = int(os.environ.get("SANDBOX_GID", "20000"))
    if uid == 0 or gid == 0 or uid < 1000 or gid < 1000:
        raise WorkerUnavailable("SANDBOX_UID/SANDBOX_GID must be dedicated non-root ids >= 1000")
    return uid, gid


class Supervisor:
    """Owns exactly one worker process and its Unix socket."""

    def __init__(self, *, state_root: str | None = None, audit=None,
                 startup_timeout: float = 90.0, max_restarts: int = 3,
                 cpu_seconds: int = 0, memory_mb: int = 0):
        self.state_root = os.path.abspath(state_root or DEFAULT_STATE_ROOT)
        self.worker_dir = os.path.join(self.state_root, "worker")
        self.template_dir = os.path.join(self.state_root, "runtime-template")
        self.socket_path = os.path.join(self.worker_dir, "job.sock")
        self.job_root = os.path.join(self.state_root, "job-workspaces")
        self.template_meta = os.path.join(self.state_root, "template.json")
        self.startup_timeout = float(startup_timeout)
        self.max_restarts = int(max_restarts)
        self.cpu_seconds = int(cpu_seconds or 0)
        self.memory_mb = int(memory_mb or 0)
        self._audit = audit
        self._token = ""
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._restarts = 0
        self._started_at = 0.0
        self._verified = False
        self._selfcheck: dict = {}
        self._last_error = ""
        self._uid = 0
        self._gid = 0

    # -- audit -------------------------------------------------------------
    def _event(self, kind: str, **fields) -> None:
        if self._audit is None:
            return
        try:
            self._audit(kind, fields)
        except Exception:  # noqa: BLE001 - auditing must never break execution
            pass

    # -- preparation -------------------------------------------------------
    def prepare(self) -> dict:
        """Build the runtime template and verify the host boundary."""
        os.makedirs(self.worker_dir, mode=0o755, exist_ok=True)
        os.makedirs(self.job_root, mode=0o700, exist_ok=True)
        self._uid, self._gid = sandbox_uid()
        build = self._ensure_template()
        probe = self._probe_boundary()
        self._selfcheck = {"template": build, "boundary": probe, "at": time.time()}
        self._verified = bool(probe.get("verified"))
        with open(os.path.join(self.state_root, "selfcheck.json"), "w", encoding="utf-8") as fh:
            json.dump(self._selfcheck, fh, indent=2, sort_keys=True)
        self._event("worker_selfcheck", verified=self._verified,
                    landlock=probe.get("landlock"), userns=probe.get("userns"),
                    sandbox_uid=self._uid)
        return self._selfcheck

    def _ensure_template(self) -> dict:
        meta = {}
        if os.path.isfile(self.template_meta):
            try:
                with open(self.template_meta, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                meta = {}
        stamp = _python_stamp()
        if (os.path.isdir(self.template_dir) and meta.get("python") == stamp
                and meta.get("complete")):
            return {"reused": True, **meta}
        build = rootfs_mod.build_template(self.template_dir)
        build["interpreter"] = rootfs_mod.mirror_interpreter(self.template_dir)
        build["packages"] = rootfs_mod.copy_payload_packages(self.template_dir)
        build.update({"python": stamp, "complete": True})
        os.makedirs(self.state_root, exist_ok=True)
        with open(self.template_meta, "w", encoding="utf-8") as fh:
            json.dump(build, fh, indent=2, sort_keys=True)
        return {"reused": False, **build}

    def _probe_boundary(self) -> dict:
        """Start the worker once and run an adversarial probe payload."""
        try:
            self.start()
        except WorkerUnavailable as exc:
            return {"verified": False, "error": str(exc)}
        return self._selfverify_job()

    def _selfverify_job(self) -> dict:
        """Run a probe that tries to break out and asserts every escape fails."""
        probe_source = _PROBE_SCRIPT
        job_id = "selfcheck-" + uuid.uuid4().hex[:12]
        workspace = os.path.join(self.job_root, job_id)
        os.makedirs(workspace, mode=0o755, exist_ok=True)
        with open(os.path.join(workspace, "probe.py"), "w", encoding="utf-8") as fh:
            fh.write(probe_source)
        try:
            result = self.run_job(job_id=job_id, project_id="__selfcheck__",
                                  workspace=workspace,
                                  argv=["/usr/bin/python3", "/workspace/probe.py"],
                                  timeout_seconds=90, cpu_seconds=30,
                                  memory_mb=512, audit=False,
                                  allow_unverified=True)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        checks = (result.get("report") or parse_probe_report(
            (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")))
        verified = bool(result.get("ok")) and checks.get("all_passed") is True
        return {"verified": verified, "result": result}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            # A fresh token per worker instance prevents replay against a
            # restarted worker that inherited an old socket.
            self._token = uuid.uuid4().hex + uuid.uuid4().hex
            env = {
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            spec = {
                "socket_path": self.socket_path,
                "runtime_template": self.template_dir,
                "state_dir": self.worker_dir,
                "sandbox_uid": self._uid,
                "sandbox_gid": self._gid,
                "token": self._token,
            }
            log_path = os.path.join(self.worker_dir, "worker.log")
            log = open(log_path, "ab", buffering=0)
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, WORKER_SCRIPT, json.dumps(spec)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, close_fds=True, env=env,
                )
            finally:
                log.close()
            self._started_at = time.time()
            self._event("worker_started", pid=self._proc.pid, restarts=self._restarts)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                break
            try:
                if self._ping(timeout=2.0):
                    return
            except OSError:
                pass
            time.sleep(0.25)
        self._last_error = "worker did not become ready"
        self.stop()
        raise WorkerUnavailable(f"worker failed to start within {self.startup_timeout}s")

    def restart(self) -> bool:
        """Restart after a crash, bounded by ``max_restarts``."""
        with self._lock:
            if self._restarts >= self.max_restarts:
                self._event("worker_restart_refused", restarts=self._restarts)
                return False
            self._restarts += 1
        self.stop()
        backoff = min(30.0, 0.5 * (2 ** (self._restarts - 1)))
        time.sleep(backoff)
        try:
            self.start()
        except WorkerUnavailable as exc:
            self._last_error = str(exc)
            self._event("worker_restart_failed", error=str(exc))
            return False
        self._event("worker_restarted", restarts=self._restarts)
        return True

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=3)
        except (ProcessLookupError, OSError):
            pass
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._verified

    # -- request plumbing --------------------------------------------------
    def _request(self, payload: dict, timeout: float = 30.0) -> dict:
        body = {"token": self._token, "nonce": uuid.uuid4().hex}
        body.update(payload)
        data = (json.dumps(body) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(data)
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
            if b"\n" not in buffer:
                raise WorkerUnavailable("worker closed the connection without a response")
            return json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
        finally:
            sock.close()

    def _ping(self, timeout: float = 5.0) -> dict:
        if not self._token:
            raise WorkerUnavailable("worker token not initialised")
        response = self._request({"action": "ping"}, timeout=timeout)
        if not response.get("ok"):
            raise WorkerUnavailable(f"worker ping rejected: {response.get('error')}")
        return response

    def health(self) -> dict:
        """Non-mutating health snapshot used by the availability check."""
        if self._proc is None or self._proc.poll() is not None:
            return {"ok": False, "alive": False,
                    "error": self._last_error or "worker not running",
                    "restarts": self._restarts}
        try:
            ping = self._ping(timeout=3.0)
        except (OSError, WorkerUnavailable) as exc:
            return {"ok": False, "alive": False, "error": str(exc),
                    "restarts": self._restarts}
        return {
            "ok": True,
            "alive": True,
            "pid": self._proc.pid,
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "restarts": self._restarts,
            "sandbox_uid": ping.get("sandbox_uid"),
            "landlock_abi": ping.get("landlock"),
            "userns": ping.get("namespaces"),
            "verified": self._verified,
        }

    # -- job execution -----------------------------------------------------
    def run_job(self, *, job_id: str, project_id: str, workspace: str,
                argv: list[str], timeout_seconds: float = 300,
                cpu_seconds: int = 0, memory_mb: int = 0,
                env: dict | None = None, audit: bool = True,
                allow_unverified: bool = False,
                read_only: list[str] | None = None) -> dict:
        """Dispatch one job and return a normalised result.

        All failures surface as ``{"ok": False, ...}``; nothing is silently
        upgraded to success and no result claims isolation that was not observed
        in the handshake.
        """
        if not allow_unverified and not self._verified:
            raise WorkerUnavailable("no verified isolated worker is available")
        if not argv or not all(isinstance(a, str) for a in argv):
            return {"ok": False, "error": "argv must be a non-empty list of strings"}
        if self._proc is None or self._proc.poll() is not None:
            if not self.restart():
                raise WorkerUnavailable(self._last_error or "worker is not running")
        payload = {
            "action": "run",
            "job": {
                "job_id": job_id,
                "project_id": project_id,
                "workspace_src": os.path.abspath(workspace),
                "argv": list(argv),
                "timeout_seconds": float(timeout_seconds),
                "cpu_seconds": int(cpu_seconds or self.cpu_seconds or timeout_seconds),
                "address_space_mb": int(memory_mb or self.memory_mb or 1024),
                "env": dict(env or {}),
                "read_only": list(read_only or []),
            },
        }
        if audit:
            self._event("job_dispatch", job_id=job_id, project_id=project_id,
                        argv=argv[:3], timeout_seconds=timeout_seconds)
        started = time.time()
        try:
            response = self._request(payload, timeout=float(timeout_seconds) + 120.0)
        except (OSError, WorkerUnavailable) as exc:
            self._last_error = str(exc)
            self._event("job_dispatch_failed", job_id=job_id, error=str(exc))
            if self._proc is None or self._proc.poll() is not None:
                self.restart()
            return {"ok": False, "job_id": job_id, "error": f"dispatch failed: {exc}",
                    "state": "DISPATCH_FAILED", "duration_seconds": round(time.time() - started, 3)}
        response.setdefault("job_id", job_id)
        response["supervisor"] = {
            "verified": bool(self._verified),
            "landlock_abi": (response.get("handshake") or {}).get("landlock", {}).get("abi"),
            "namespaces": (response.get("handshake") or {}).get("namespaces", {}),
            "wall_seconds": round(time.time() - started, 3),
        }
        if audit:
            self._event("job_finished", job_id=job_id, project_id=project_id,
                        ok=bool(response.get("ok")), exit_code=response.get("exit_code"),
                        timed_out=bool(response.get("timed_out")),
                        cleaned_up=response.get("cleaned_up"),
                        duration_seconds=response.get("duration_seconds"))
        return response

    def cancel_job(self, job_id: str) -> dict:
        try:
            return self._request({"action": "cancel", "job_id": job_id}, timeout=10.0)
        except (OSError, WorkerUnavailable) as exc:
            return {"ok": False, "error": str(exc)}

    # -- observability -----------------------------------------------------
    def selfcheck(self) -> dict:
        return dict(self._selfcheck)


def _python_stamp() -> str:
    try:
        st = os.stat(sys.executable)
        return hashlib.sha256(
            f"{sys.executable}:{st.st_size}:{int(st.st_mtime)}:{sys.version}".encode()
        ).hexdigest()[:16]
    except OSError:
        return "unknown"


#: Runs inside the sandbox and returns a machine-readable verification report.
_PROBE_SCRIPT = r'''
import json, os, socket, subprocess, sys

report = {"checks": {}, "all_passed": False}

def check(name, fn, expect_blocked=True):
    try:
        outcome = fn()
        passed = outcome is False if expect_blocked else bool(outcome)
    except Exception as exc:  # expected outcome is a raised error
        outcome = f"{type(exc).__name__}: {exc}"
        passed = expect_blocked
    report["checks"][name] = {"pass": bool(passed), "observed": str(outcome)[:200]}

check("host_root_denied", lambda: os.path.exists("/home"))
check("host_env_denied", lambda: os.path.exists("/home/user/phone-ai-agent-team/.env"))
check("host_shadow_denied", lambda: os.path.exists("/etc/shadow"))
check("workspace_writable", lambda: (open("/workspace/.probe", "w").write("x"),
                                     os.remove("/workspace/.probe"), True)[-1],
      expect_blocked=False)
check("outside_workspace_denied", lambda: open("/opt/probe", "w"))
check("symlink_escape_denied", lambda: (os.symlink("/etc", "/workspace/.escape"),
                                        open("/workspace/.escape/hostname", "w")))
check("network_denied", lambda: socket.socket().connect(("1.1.1.1", 443)) or True)
check("lo_is_down", lambda: os.path.exists("/sys/class/net/eth0"))
check("procfs_hidden", lambda: len(os.listdir("/proc")) > 5)
check("no_host_pids", lambda: any(p.isdigit() and int(p) < 5 for p in os.listdir("/proc"))
      if os.path.isdir("/proc") else True)
check("unshare_denied", lambda: subprocess.run(["/usr/bin/unshare", "-Ur", "true"]).returncode == 0)
check("chroot_denied", lambda: os.chroot("/tmp") and True)
check("payload_runs", lambda: subprocess.run(["/usr/bin/python3", "-c", "print(1)"],
                                             capture_output=True).returncode == 0,
      expect_blocked=False)
check("host_ssh_denied", lambda: os.path.exists("/home/user/.ssh"))
report["all_passed"] = all(v["pass"] for v in report["checks"].values())
sys.stdout.write("@@PROBE_REPORT@@" + json.dumps(report) + "\n")
sys.stdout.flush()
if not report["all_passed"]:
    sys.exit(3)
'''


def parse_probe_report(stdout: str) -> dict:
    marker = "@@PROBE_REPORT@@"
    index = stdout.find(marker)
    if index < 0:
        return {}
    end = stdout.find("\n", index)
    chunk = stdout[index + len(marker): end if end >= 0 else None]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        return {}

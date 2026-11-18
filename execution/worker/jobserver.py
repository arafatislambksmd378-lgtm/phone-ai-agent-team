"""Worker-side job server: receives job requests, runs them confined, returns results.

This program runs *inside* the worker's boundary and is started only by
``supervisor.Supervisor``. It never executes untrusted code in its own process:
every request is executed by a fresh ``launcher`` child, so the server keeps
running (and can report a crash) even if a payload kills its own process group.

Transport
---------
A Unix domain socket whose parent directory is mode 0700 and owned by the
service user. The supervisor additionally authenticates with a per-worker HMAC
token so a stray process that can reach the socket (same uid only) still cannot
submit jobs without the token. Every request is nonce-tagged and the nonce is
remembered, so replayed frames are rejected even for a valid token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import resource
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

MAX_REQUEST_BYTES = 256 * 1024
MAX_CONCURRENT_JOBS = 2
JOB_SCRATCH_ROOT = "/tmp/phone-agent-jobs"


def _now() -> float:
    return time.time()


class JobServer:
    def __init__(self, socket_path: str, runtime_template: str, state_dir: str,
                 sandbox_uid: int, sandbox_gid: int, token: str):
        self.socket_path = socket_path
        self.runtime_template = runtime_template
        self.state_dir = state_dir
        self.sandbox_uid = int(sandbox_uid)
        self.sandbox_gid = int(sandbox_gid)
        self._token = token
        self._seen_nonces: dict[str, float] = {}
        self._lock = threading.Lock()
        self._running = True
        self._jobs: dict[str, dict] = {}
        self._job_lock = threading.Lock()
        os.makedirs(JOB_SCRATCH_ROOT, exist_ok=True)
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(os.path.join(JOB_SCRATCH_ROOT, "jobs"), exist_ok=True)

    # -- lifecycle ---------------------------------------------------------
    def serve_forever(self) -> None:
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(1.0)
        _write_state(self.state_dir, {"status": "READY", "socket": self.socket_path,
                                      "pid": os.getpid(), "since": _now()})
        self._ready = True
        while self._running:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
        try:
            server.close()
        finally:
            _write_state(self.state_dir, {"status": "STOPPED", "pid": os.getpid(),
                                          "since": _now()})
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

    def stop(self) -> None:
        self._running = False

    # -- request handling --------------------------------------------------
    def _handle_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            buffer = b""
            while b"\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > MAX_REQUEST_BYTES:
                    _send(conn, {"ok": False, "error": "request too large"})
                    return
            line, _rest = buffer.split(b"\n", 1)
            try:
                request = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _send(conn, {"ok": False, "error": "malformed request"})
                return
            response = self._dispatch(request)
            _send(conn, response)
        except (OSError, socket.timeout):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, request: dict) -> dict:
        action = request.get("action")
        if action == "ping":
            if not self._verify(request):
                return {"ok": False, "error": "unauthorized"}
            return {
                "ok": True,
                "status": "READY",
                "sandbox_uid": self.sandbox_uid,
                "landlock": _landlock_probe(),
                "namespaces": _userns_probe(),
                "template": os.path.isdir(self.runtime_template),
                "active_jobs": len(self._jobs),
                "load": _loadavg(),
                "pid": os.getpid(),
            }
        if action == "run":
            if not self._verify(request):
                return {"ok": False, "error": "unauthorized"}
            job = request.get("job") or {}
            return self._run_job(job)
        if action == "cancel":
            if not self._verify(request):
                return {"ok": False, "error": "unauthorized"}
            return self._cancel_job(str(request.get("job_id") or ""))
        if action == "status":
            if not self._verify(request):
                return {"ok": False, "error": "unauthorized"}
            with self._job_lock:
                return {"ok": True, "jobs": {k: {"state": v.get("state")}
                                             for k, v in self._jobs.items()}}
        return {"ok": False, "error": f"unknown action {action!r}"}

    def _verify(self, request: dict) -> bool:
        token = str(request.get("token") or "")
        nonce = str(request.get("nonce") or "")
        if not token or not nonce or len(nonce) < 16:
            return False
        if not hmac.compare_digest(token, self._token):
            return False
        with self._lock:
            if nonce in self._seen_nonces:
                return False
            self._seen_nonces[nonce] = _now()
            cutoff = _now() - 900
            for key in [k for k, ts in self._seen_nonces.items() if ts < cutoff]:
                self._seen_nonces.pop(key, None)
        return True

    # -- job execution -----------------------------------------------------
    def _run_job(self, job: dict) -> dict:
        job_id = str(job.get("job_id") or "")
        if not job_id or not _is_safe_id(job_id):
            return {"ok": False, "error": "invalid job id"}
        with self._job_lock:
            if job_id in self._jobs:
                return {"ok": False, "error": "duplicate job id"}
            if len(self._jobs) >= MAX_CONCURRENT_JOBS:
                return {"ok": False, "error": "worker busy", "retryable": True}
            self._jobs[job_id] = {"state": "STARTING", "started": _now()}
        try:
            result = self._execute(job_id, job)
        except Exception as exc:  # noqa: BLE001 - must always answer
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "state": "ERROR"}
        finally:
            with self._job_lock:
                self._jobs.pop(job_id, None)
        return result

    def _execute(self, job_id: str, job: dict) -> dict:
        import rootfs as rootfs_mod  # local import: deployed beside this file

        workspace_src = os.path.abspath(str(job.get("workspace_src") or ""))
        if not workspace_src or not os.path.isdir(workspace_src):
            return {"ok": False, "error": "workspace_src missing"}
        job_dir = os.path.join(JOB_SCRATCH_ROOT, "jobs", job_id)
        job_root = os.path.join(job_dir, "root")
        shutil.rmtree(job_dir, ignore_errors=True)
        os.makedirs(job_dir, mode=0o700, exist_ok=True)

        build_started = _now()
        try:
            rootfs_mod.clone_for_job(self.runtime_template, job_root)
            rootfs_mod.materialise_workspace(job_root, workspace_src,
                                             self.sandbox_uid, self.sandbox_gid)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(job_dir, ignore_errors=True)
            return {"ok": False, "error": f"rootfs: {exc}", "state": "ROOTFS_ERROR"}
        build_seconds = round(_now() - build_started, 3)

        timeout = min(max(float(job.get("timeout_seconds") or 300), 5.0), 3600.0)
        harness_spec = {
            "root": job_root,
            "workspace": "/workspace",
            # Inside the disposable root "/" is the chroot boundary; Landlock
            # allows it read-only and grants write access only to the workspace
            # and scratch space.
            # Site-packages built into the template (pytest and friends) are
            # addressed through the *host* path in PYTHONPATH/PYTHONHOME, so they
            # need their own read-only rule.
            "read_only": [p for p in (job.get("read_only") or []) if p],
            "read_execute": ["/"],
            "writable": ["/workspace", "/tmp", "/dev/null"],
            "cpu_seconds": int(job.get("cpu_seconds") or timeout),
            "address_space_mb": int(job.get("address_space_mb") or 1024),
            "process_count": int(job.get("process_count") or 128),
            "file_size_mb": int(job.get("file_size_mb") or 32),
            "open_files": int(job.get("open_files") or 256),
            "argv": list(job.get("argv") or []),
            "env": _sanitise_env(job.get("env") or {}, job_root, sys.prefix != sys.base_prefix),
        }
        launcher_spec = {
            "root": job_root,
            "sandbox_uid": self.sandbox_uid,
            "sandbox_gid": self.sandbox_gid,
            "harness": os.path.join(_HERE, "harness.py"),
            "harness_spec": harness_spec,
        }

        started = _now()
        with self._job_lock:
            self._jobs[job_id]["state"] = "RUNNING"
        proc = subprocess.Popen(
            [sys.executable, os.path.join(_HERE, "launcher.py"), json.dumps(launcher_spec)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            cwd=job_dir,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"},
        )
        with self._job_lock:
            self._jobs[job_id]["proc"] = proc
        handshake: dict = {}
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
            rc = -signal.SIGKILL
        elapsed = round(_now() - started, 3)
        stdout_bytes = out or b""
        stderr_bytes = err or b""
        stdout_text, handshake = _split_handshake(stdout_bytes)
        workspace_size = rootfs_mod.workspace_bytes(os.path.join(job_root, "workspace"))

        result = {
            "ok": (not timed_out) and rc == 0 and handshake.get("status") == "ok",
            "job_id": job_id,
            "exit_code": rc,
            "timed_out": timed_out,
            "duration_seconds": elapsed,
            "rootfs_build_seconds": build_seconds,
            "stdout": _cap(stdout_text, 64 * 1024),
            "stderr": _cap(stderr_bytes.decode("utf-8", "replace"), 32 * 1024),
            "handshake": handshake,
            "workspace_bytes": workspace_size,
            "state": "COMPLETED" if rc == 0 else "FAILED",
            "cleanup_seconds": 0.0,
        }
        cleanup_started = _now()
        shutil.rmtree(job_dir, ignore_errors=True)
        result["cleanup_seconds"] = round(_now() - cleanup_started, 3)
        result["cleaned_up"] = not os.path.exists(job_dir)
        if timed_out:
            result["error"] = f"job exceeded {int(timeout)}s and was killed"
        elif handshake.get("status") != "ok":
            result["error"] = "sandbox handshake missing or failed (fail-closed)"
        return result

    def _cancel_job(self, job_id: str) -> dict:
        with self._job_lock:
            entry = self._jobs.get(job_id)
            proc = (entry or {}).get("proc")
        if not proc:
            return {"ok": False, "error": "unknown job"}
        _kill_group(proc)
        return {"ok": True, "job_id": job_id, "cancelled": True}


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _split_handshake(stdout: bytes) -> tuple[str, dict]:
    from harness import HANDSHAKE_PREFIX
    prefix = HANDSHAKE_PREFIX.encode() + b" "
    index = stdout.find(prefix)
    if index < 0:
        return stdout.decode("utf-8", "replace"), {}
    end = stdout.find(b"\n", index)
    if end < 0:
        return "", {}
    line = stdout[index + len(prefix):end]
    rest = stdout[:index] + stdout[end + 1:]
    try:
        return rest.decode("utf-8", "replace"), json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return rest.decode("utf-8", "replace"), {}


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"[truncated {len(text) - limit} of {len(text)} bytes]\n" + text[-limit:]


def _sanitise_env(env: dict, job_root: str = "", is_venv: bool = False) -> dict:
    allowed = {
        "PATH", "LANG", "LC_ALL", "TZ", "TERM", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED", "PYTHONHASHSEED", "PYTHONNOUSERSITE", "PYTEST_ADDOPTS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
        "MPLBACKEND",
    }
    clean = {}
    for key, value in (env or {}).items():
        key = str(key)
        if key in allowed and len(str(value)) < 4096:
            clean[key] = str(value)
    clean.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    clean.setdefault("HOME", "/workspace")
    clean.setdefault("TMPDIR", "/tmp")
    if is_venv:
        # The payload interpreter is the mirrored venv one, which must find its
        # pyvenv.cfg at the template root.
        clean["VIRTUAL_ENV"] = "/virtualenv"
    if job_root:
        # Path variables are rewritten so they address *host* locations that the
        # pre-chroot Landlock policy already allows: /workspace becomes this
        # job's root and any non-template site-packages path becomes the
        # template copy. This keeps PYTHONPATH valid both before and after the
        # chroot, because both sides resolve to the same inodes.
        clean = {key: _rewrite_paths(value, job_root)
                 for key, value in clean.items()
                 if key in _PATH_VARS and key != "VIRTUAL_ENV"}
    return clean


#: Only genuinely path-valued variables may be rewritten.
_PATH_VARS = {"PYTHONPATH", "PYTHONHOME", "TMPDIR", "PATH"}


def _rewrite_paths(value: str, job_root: str) -> str:
    parts = []
    for part in str(value).split(os.pathsep):
        if not part:
            continue
        if part == "/virtualenv" or part.startswith("/virtualenv/"):
            parts.append(job_root + part)
        elif part == "/workspace" or part.startswith("/workspace/"):
            parts.append(job_root + part)
        elif part.startswith("/usr/lib/python"):
            # Map /usr/lib/pythonX.Y/site-packages onto the template's matching
            # stdlib prefix (which may not literally be usr/lib on this host).
            import sysconfig

            version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            host_libdest = (sysconfig.get_config_var("LIBDEST") or f"/usr/lib/{version}").rstrip("/")
            if part.startswith(host_libdest):
                parts.append(job_root + host_libdest + part[len(host_libdest):])
            else:
                stem = part.split("site-packages")[0]
                parts.append(os.path.join(job_root, stem.lstrip("/"), "site-packages"))
        elif part.startswith("/"):
            parts.append(job_root + part)
        else:
            parts.append(part)
    return os.pathsep.join(parts)


def _is_safe_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def _write_state(state_dir: str, payload: dict) -> None:
    path = os.path.join(state_dir, "state.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _send(conn: socket.socket, payload: dict) -> None:
    try:
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except OSError:
        pass


def _loadavg() -> list[float]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return []


def _userns_probe() -> bool:
    """True when unprivileged user namespaces are permitted on this host."""
    try:
        pid = os.fork()
    except OSError:
        return False
    if pid == 0:  # pragma: no cover - child
        try:
            os.execve("/usr/bin/unshare", ["unshare", "-Ur", "true"], {"PATH": "/usr/bin:/bin"})
        except Exception:
            os._exit(127)
    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        return False
    return os.waitstatus_to_exitcode(status) == 0


def _landlock_probe() -> int:
    import landlock
    return landlock.abi_version()


def main(argv: list[str]) -> None:
    try:
        spec = json.loads(argv[1])
    except (IndexError, json.JSONDecodeError):
        sys.stderr.write("jobserver: missing spec\n")
        sys.exit(2)

    # Keep our own memory bounded; the server itself runs no untrusted code.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    server = JobServer(
        socket_path=spec["socket_path"],
        runtime_template=spec["runtime_template"],
        state_dir=spec["state_dir"],
        sandbox_uid=int(spec["sandbox_uid"]),
        sandbox_gid=int(spec["sandbox_gid"]),
        token=spec["token"],
    )

    def _stop(signum, frame):  # noqa: ARG001
        server.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv)

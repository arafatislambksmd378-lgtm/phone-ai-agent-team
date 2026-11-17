"""Process manager — track subprocesses, enforce timeouts & output caps, kill cleanly on stop."""
from __future__ import annotations
import subprocess
import threading
import time

MAX_PROC_OUTPUT = 8000


class ProcessManager:
    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}
        self._started_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def register(self, key: str, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs[key] = proc
            self._started_at[key] = time.time()

    def unregister(self, key: str) -> None:
        with self._lock:
            self._procs.pop(key, None)
            self._started_at.pop(key, None)

    def stop(self, key: str) -> bool:
        with self._lock:
            proc = self._procs.pop(key, None)
            self._started_at.pop(key, None)
        if proc is None:
            return False
        if proc.poll() is not None:
            return True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        return True

    def stop_all(self) -> int:
        with self._lock:
            keys = list(self._procs.keys())
        n = 0
        for k in keys:
            if self.stop(k):
                n += 1
        return n

    def running(self) -> list[str]:
        with self._lock:
            alive = [k for k, p in self._procs.items() if p.poll() is None]
            # Prune exited entries
            for k in list(self._procs.keys()):
                if k not in alive:
                    self._procs.pop(k, None)
                    self._started_at.pop(k, None)
            return alive

    def run_managed(self, key: str, cmd: list[str], cwd: str, timeout: int,
                    env: dict | None = None) -> dict:
        """Spawn a tracked subprocess without shell, enforce timeout, and guarantee cleanup."""
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        self.register(key, proc)
        try:
            out, err = proc.communicate(timeout=max(1, int(timeout)))
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": (out or "")[-MAX_PROC_OUTPUT:],
                "stderr": (err or "")[-MAX_PROC_OUTPUT:],
            }
        except subprocess.TimeoutExpired:
            self.stop(key)
            return {"ok": False, "error": f"Timeout after {timeout}s", "stdout": "", "stderr": "",
                    "timed_out": True}
        finally:
            self.unregister(key)


def run_with_timeout(cmd: list[str], cwd: str, timeout: int,
                     process_manager: ProcessManager | None = None,
                     env: dict | None = None) -> dict:
    if process_manager is not None:
        return process_manager.run_managed(f"cmd_{time.monotonic_ns()}", cmd, cwd, timeout, env=env)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-MAX_PROC_OUTPUT:], "stderr": proc.stderr[-MAX_PROC_OUTPUT:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout", "stdout": "", "stderr": ""}

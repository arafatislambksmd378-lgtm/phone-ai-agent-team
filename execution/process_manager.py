"""Process manager — track subprocesses, enforce timeouts, kill on stop."""
from __future__ import annotations
import subprocess
import threading
import time

class ProcessManager:
    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def register(self, key: str, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs[key] = proc

    def stop(self, key: str) -> bool:
        with self._lock:
            proc = self._procs.pop(key, None)
        if proc is None:
            return False
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True

    def stop_all(self) -> int:
        keys = list(self._procs.keys())
        n = 0
        for k in keys:
            if self.stop(k):
                n += 1
        return n

    def running(self) -> list[str]:
        with self._lock:
            return [k for k, p in self._procs.items() if p.poll() is None]

def run_with_timeout(cmd: list[str], cwd: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout", "stdout": "", "stderr": ""}

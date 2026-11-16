"""Scheduler — resource-aware worker loop. Default 1 worker (3GB RAM)."""
from __future__ import annotations
import threading
import time
from memory.database import Database

class Scheduler:
    def __init__(self, db: Database, max_workers: int = 1):
        self.db = db
        self.max_workers = max(1, min(2, max_workers))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_fn = None

    def set_worker(self, fn) -> None:
        self._worker_fn = fn

    def start(self) -> None:
        self._stop.clear()
        for i in range(self.max_workers):
            t = threading.Thread(target=self._loop, args=(f"worker-{i}",), daemon=True, name=f"scheduler-{i}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads = []

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            task = self.db.claim_task(worker_id)
            if task is None:
                time.sleep(0.5)
                continue
            if self._worker_fn:
                try:
                    self._worker_fn(task)
                except Exception:
                    self.db.set_task_status(task["id"], "FAILED", error="Worker exception")
            else:
                self.db.set_task_status(task["id"], "COMPLETED", result="No worker bound")

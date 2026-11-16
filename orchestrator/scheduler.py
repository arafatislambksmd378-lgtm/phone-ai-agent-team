"""Scheduler — resource-aware worker pool. Default 1 worker (3 GB RAM).

Workers never stop the pool themselves: the engine decides when a project is
finished, so the final task is always recorded before shutdown.
"""
from __future__ import annotations
import logging
import threading
import time
from memory.database import Database

log = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, db: Database, max_workers: int = 1, idle_sleep: float = 0.3):
        self.db = db
        self.max_workers = max(1, min(2, int(max_workers)))
        self.idle_sleep = idle_sleep
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_fn = None
        self._active = 0
        self._active_lock = threading.Lock()
        self._on_idle = None

    # ---- configuration ----
    def set_worker(self, fn) -> None:
        self._worker_fn = fn

    def set_idle_callback(self, fn) -> None:
        """Called when the pool has no active work (used for clean auto-stop)."""
        self._on_idle = fn

    def is_running(self) -> bool:
        return len(self._threads) > 0 and any(t.is_alive() for t in self._threads)

    def active_count(self) -> int:
        with self._active_lock:
            return self._active

    # ---- lifecycle ----
    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(target=self._loop, args=(f"worker-{i}",), daemon=True,
                                 name=f"scheduler-{i}")
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        for t in list(self._threads):
            if t is not threading.current_thread():
                t.join(timeout=timeout)
        self._threads = []

    # ---- loop ----
    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            task = self.db.claim_task(worker_id)
            if task is None:
                if self._on_idle is not None:
                    try:
                        self._on_idle()
                    except Exception:
                        log.exception("idle callback failed")
                self._stop.wait(self.idle_sleep)
                continue
            with self._active_lock:
                self._active += 1
            try:
                if self._worker_fn:
                    self._worker_fn(task)
                else:
                    self.db.set_task_status(task["id"], "FAILED", error="No worker bound")
            except Exception as e:  # never lose a claimed task
                log.exception("worker failed on task %s", task.get("id"))
                self.db.set_task_status(task["id"], "FAILED", error=f"Worker exception: {e}")
            finally:
                with self._active_lock:
                    self._active -= 1

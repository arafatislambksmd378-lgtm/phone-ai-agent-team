"""Scheduler — Android 3 GB RAM resource-aware worker pool.

Default concurrency is 1 active worker. A 2nd worker is permitted ONLY when
`ResourceManager.effective_worker_limit()` confirms sufficient free RAM, CPU, and storage.
Workers never stop the pool from inside their own thread: the engine closes projects
and stops the pool cleanly when all runnable work finishes.
"""
from __future__ import annotations
import logging
import threading
from memory.database import Database

log = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, db: Database, max_workers: int = 1, idle_sleep: float = 0.25,
                 resource_manager=None, stale_after: float = 900.0):
        self.db = db
        self.configured_max_workers = max(1, min(2, int(max_workers)))
        self.resource_manager = resource_manager
        self.idle_sleep = idle_sleep
        self.stale_after = max(0.0, float(stale_after))
        self.max_workers = self._compute_limit() if resource_manager is not None else self.configured_max_workers
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_fn = None
        self._active = 0
        self._active_lock = threading.Lock()
        self._on_idle = None

    def _compute_limit(self) -> int:
        if self.resource_manager is None:
            return self.configured_max_workers
        return self.resource_manager.effective_worker_limit(self.configured_max_workers)

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
        self.max_workers = self._compute_limit()
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._loop, args=(f"worker-{i}", i), daemon=True, name=f"scheduler-{i}"
            )
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        for t in list(self._threads):
            if t is not threading.current_thread():
                t.join(timeout=timeout)
        self._threads = []

    # ---- loop ----
    def _loop(self, worker_id: str, worker_index: int = 0) -> None:
        while not self._stop.is_set():
            # Secondary worker yields dynamically if phone RAM/CPU tightens mid-run
            if worker_index > 0 and self.resource_manager is not None:
                if not self.resource_manager.can_enable_second_worker():
                    self._stop.wait(self.idle_sleep * 2)
                    continue

            task = self.db.claim_task(worker_id, stale_after=self.stale_after)
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

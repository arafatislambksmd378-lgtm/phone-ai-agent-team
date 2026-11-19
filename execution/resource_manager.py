"""Resource Manager — tuned for Android 14 / 3 GB RAM / 64 GB storage / Termux.

Responsibilities:
  * Monitor system & process RAM (/proc/meminfo + RSS), CPU load, and free storage.
  * Enforce default 1 active worker; permit optional 2nd worker ONLY when live resource
    checks pass (available RAM >= threshold, disk free >= threshold, CPU load healthy).
  * Detect Termux & installed build toolchains honestly (never promise unavailable SDKs).
  * Reclaim memory (gc.collect) and purge temporary/stale files safely.
"""
from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time
from pathlib import Path

from execution.process_manager import ProcessManager


class ResourceManager:
    def __init__(self, workspace_root: str | Path = "./projects",
                 min_free_ram_mb_worker2: int = 600,
                 min_free_disk_mb: int = 250,
                 critical_free_ram_mb: int = 120,
                 process_manager: ProcessManager | None = None):
        self.workspace_root = Path(workspace_root)
        self.min_free_ram_mb_worker2 = max(128, int(min_free_ram_mb_worker2))
        self.min_free_disk_mb = max(50, int(min_free_disk_mb))
        self.critical_free_ram_mb = max(48, int(critical_free_ram_mb))
        self.processes = process_manager or ProcessManager()

    # ---------- environment & toolchain detection ----------
    @staticmethod
    def is_termux() -> bool:
        prefix = os.environ.get("PREFIX", "")
        return "com.termux" in prefix or Path("/data/data/com.termux").exists()

    @staticmethod
    def detect_toolchains() -> dict[str, bool]:
        tools = ("python3", "pytest", "git", "node", "npm", "gradle", "kotlinc", "javac", "aapt2")
        return {t: shutil.which(t) is not None for t in tools}

    # ---------- metrics ----------
    def memory_snapshot(self) -> dict[str, float]:
        """Return RAM figures in MB using /proc/meminfo and getrusage (stdlib only)."""
        total_mb = 0.0
        avail_mb = 0.0
        try:
            meminfo = Path("/proc/meminfo")
            if meminfo.exists():
                kv: dict[str, int] = {}
                for line in meminfo.read_text(encoding="ascii", errors="ignore").splitlines():
                    if ":" not in line:
                        continue
                    k, rest = line.split(":", 1)
                    parts = rest.strip().split()
                    if parts and parts[0].isdigit():
                        kv[k.strip()] = int(parts[0])  # kB
                total_mb = round(kv.get("MemTotal", 0) / 1024.0, 1)
                avail_kb = kv.get("MemAvailable")
                if avail_kb is None:
                    avail_kb = kv.get("MemFree", 0) + kv.get("Buffers", 0) + kv.get("Cached", 0)
                avail_mb = round(avail_kb / 1024.0, 1)
        except OSError:
            pass

        rss_mb = 0.0
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux reports ru_maxrss in KiB; macOS in bytes
            rss_mb = round(ru / (1024.0 * 1024.0) if ru > 10_000_000 else ru / 1024.0, 1)
        except Exception:
            pass

        return {"total_mb": total_mb, "available_mb": avail_mb, "process_rss_mb": rss_mb}

    def cpu_snapshot(self) -> dict[str, float]:
        cores = float(os.cpu_count() or 1)
        load_1m = 0.0
        try:
            load_1m = round(os.getloadavg()[0], 2)
        except (AttributeError, OSError):
            pass
        return {"cores": cores, "load_1m": load_1m}

    def storage_snapshot(self, target: str | Path | None = None) -> dict[str, float]:
        check_path = Path(target or self.workspace_root)
        check_path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(str(check_path))
        return {
            "total_mb": round(usage.total / (1024 * 1024), 1),
            "used_mb": round(usage.used / (1024 * 1024), 1),
            "free_mb": round(usage.free / (1024 * 1024), 1),
        }

    def snapshot(self) -> dict:
        return {
            "is_termux": self.is_termux(),
            "memory": self.memory_snapshot(),
            "cpu": self.cpu_snapshot(),
            "storage": self.storage_snapshot(),
            "active_subprocesses": len(self.processes.running()),
        }

    # ---------- concurrency & admission gates ----------
    def effective_worker_limit(self, requested_workers: int = 1) -> int:
        """Default 1 worker; allow 2nd worker ONLY when RAM, CPU, and disk checks pass."""
        req = max(1, min(2, int(requested_workers or 1)))
        if req == 1:
            return 1
        if self.can_enable_second_worker():
            return 2
        return 1

    def can_enable_second_worker(self) -> bool:
        mem = self.memory_snapshot()
        disk = self.storage_snapshot()
        cpu = self.cpu_snapshot()
        avail = mem["available_mb"]
        if avail > 0 and avail < self.min_free_ram_mb_worker2:
            return False
        if disk["free_mb"] < self.min_free_disk_mb:
            return False
        if cpu["load_1m"] > max(2.5, cpu["cores"] * 1.2):
            return False
        return True

    def check_resources_for_task(self) -> tuple[bool, str, dict]:
        """Verify the device has enough free storage and memory before starting a task."""
        snap = self.snapshot()
        free_disk = snap["storage"]["free_mb"]
        if free_disk < self.min_free_disk_mb:
            self.cleanup_temp_files()
            snap = self.snapshot()
            free_disk = snap["storage"]["free_mb"]
            if free_disk < self.min_free_disk_mb:
                return False, f"Insufficient free storage ({free_disk} MB < {self.min_free_disk_mb} MB)", snap

        avail_ram = snap["memory"]["available_mb"]
        if 0 < avail_ram < self.critical_free_ram_mb:
            gc.collect()
            snap = self.snapshot()
            avail_ram = snap["memory"]["available_mb"]
            if 0 < avail_ram < self.critical_free_ram_mb:
                return False, f"Critically low available RAM ({avail_ram} MB)", snap
        return True, "OK", snap

    def post_task_housekeeping(self, workspace: str | Path | None = None) -> None:
        """Reclaim temporary files and run garbage collection after each agent task."""
        if workspace:
            self.cleanup_workspace_temps(workspace)
        gc.collect()

    # ---------- cleanup ----------
    def cleanup_workspace_temps(self, workspace: str | Path) -> int:
        ws = Path(workspace)
        if not ws.exists():
            return 0
        removed = 0
        for p in ws.rglob("*"):
            try:
                if p.is_file() and (p.name.endswith(".tmp") or p.name.endswith(".part")):
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
        return removed

    def cleanup_temp_files(self, max_age_seconds: int = 1800) -> int:
        """Remove stale tg_upload_* temp files and workspace *.part/*.tmp artifacts."""
        removed = 0
        now = time.time()
        tmp_dir = Path(tempfile.gettempdir())
        try:
            for f in tmp_dir.glob("tg_upload_*"):
                try:
                    if f.is_file() and (now - f.stat().st_mtime) >= max_age_seconds:
                        f.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        if self.workspace_root.exists():
            removed += self.cleanup_workspace_temps(self.workspace_root)
        return removed

    def shutdown(self) -> int:
        """Gracefully terminate all tracked child processes and clean stale temp files."""
        stopped = self.processes.stop_all()
        self.cleanup_temp_files(max_age_seconds=300)
        gc.collect()
        return stopped

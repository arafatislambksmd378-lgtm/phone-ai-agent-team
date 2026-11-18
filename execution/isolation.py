"""Fail-closed policy for executing untrusted project code.

Execution is enabled **only** when a worker can demonstrate, at runtime, all of
the following on the machine that will run it:

1. a multi-namespace boundary (mount, pid, net, ipc, uts + user namespace or a
   dedicated unprivileged uid),
2. Landlock LSM filesystem confinement (ABI >= 1) that denies host reads,
3. a disposable per-job root filesystem,
4. hard rlimits (CPU / address space / descriptors / file size / process count),
5. a seccomp deny filter,
6. no outbound network reachability.

That evidence comes from an adversarial probe payload executed inside the
sandbox (``execution/worker/supervisor.py::_PROBE_SCRIPT``). A docker flag, an
environment variable, an allowlist or a subprocess timeout is *not* evidence and
never enables execution.

Operator switches (all must be true to even attempt verification):

* ``ISOLATED_WORKER_ENABLED=true`` — explicit opt-in.
* ``ISOLATED_WORKER_BACKEND`` — currently only ``local_namespace`` (an
  OS-isolated, unprivileged worker on the service host). ``remote_agent``
  remains documented-but-unimplemented; a microVM pool is the production target
  (see ``docs/implementation/PHASE5_ISOLATED_EXECUTION.md``).
* ``SANDBOX_UID`` / ``SANDBOX_GID`` — dedicated non-root identities.

The result is cached for ``ISOLATED_WORKER_RECHECK_SECONDS`` and reused, but a
failed verification is never cached as success.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("isolation")

BLOCKED_MESSAGE = (
    "Untrusted project execution is blocked: no verified isolated worker is available "
    "(set ISOLATED_WORKER_ENABLED=true and pass the worker self-check, see "
    "docs/implementation/PHASE5_ISOLATED_EXECUTION.md)"
)

SUPPORTED_BACKENDS = ("local_namespace",)
PLANNED_BACKENDS = ("remote_agent", "firecracker")

_lock = threading.RLock()
_state: dict = {"checked_at": 0.0, "verified": False, "report": {}, "error": "not checked"}
_supervisor = None
_recheck_seconds = 300.0
_last_error = ""


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def backend() -> str:
    return (_cfg("ISOLATED_WORKER_BACKEND") or "local_namespace").lower()


def _enabled() -> bool:
    return _cfg("ISOLATED_WORKER_ENABLED").lower() in {"1", "true", "yes", "on"}


def isolated_worker_available() -> bool:
    """True only when a verified isolated worker was observed in this process."""
    if not _enabled():
        return False
    return get_supervisor() is not None


def get_supervisor(*, force_recheck: bool = False):
    """Return a ready :class:`Supervisor` or ``None``; never raises."""
    global _supervisor, _state, _last_error, _recheck_seconds
    with _lock:
        now = time.time()
        try:
            _recheck_seconds = max(30.0, float(_cfg("ISOLATED_WORKER_RECHECK_SECONDS", "300")))
        except ValueError:
            _recheck_seconds = 300.0
        if not _enabled():
            _state = {"checked_at": now, "verified": False,
                      "report": {"reason": "ISOLATED_WORKER_ENABLED is not set"},
                      "error": "disabled"}
            return None
        if backend() not in SUPPORTED_BACKENDS:
            if backend() in PLANNED_BACKENDS:
                _last_error = (
                    f"backend {backend()!r} is planned but not implemented; it cannot be "
                    "enabled (see docs/implementation/PHASE5_ISOLATED_EXECUTION.md)"
                )
            else:
                _last_error = f"unknown ISOLATED_WORKER_BACKEND {backend()!r}"
            _state = {"checked_at": now, "verified": False,
                      "report": {"reason": _last_error}, "error": _last_error}
            return None
        if (_supervisor is not None and not force_recheck
                and now - _state["checked_at"] < _recheck_seconds):
            health = _supervisor.health()
            if health.get("ok"):
                return _supervisor
            log.warning("isolated worker unhealthy: %s", health.get("error"))
            if not _supervisor.restart():
                _supervisor.stop()
                _supervisor = None
                _state = {"checked_at": now, "verified": False,
                          "report": {"reason": health.get("error")},
                          "error": health.get("error", "unhealthy")}
                return None
        try:
            from execution.worker.supervisor import Supervisor, WorkerUnavailable
        except Exception as exc:  # pragma: no cover - import environment
            _last_error = f"worker subsystem unavailable: {exc}"
            _state = {"checked_at": now, "verified": False,
                      "report": {"reason": _last_error}, "error": _last_error}
            return None
        sup = _supervisor
        try:
            if sup is None:
                sup = Supervisor()
            report = sup.prepare()
        except WorkerUnavailable as exc:
            _last_error = str(exc)
            if sup is not None:
                sup.stop()
            _state = {"checked_at": now, "verified": False,
                      "report": {"reason": _last_error}, "error": _last_error}
            log.warning("isolated worker verification failed: %s", _last_error)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            _last_error = f"{type(exc).__name__}: {exc}"
            if sup is not None:
                sup.stop()
            _state = {"checked_at": now, "verified": False,
                      "report": {"reason": _last_error}, "error": _last_error}
            log.exception("isolated worker verification crashed")
            return None
        verified = bool(report.get("boundary", {}).get("verified"))
        _supervisor = sup if verified else None
        _state = {"checked_at": now, "verified": verified, "report": report,
                  "error": "" if verified else "boundary verification failed"}
        if verified:
            log.info("isolated worker verified: %s", _describe(report))
        else:
            _last_error = "boundary verification failed"
            report = report.get("boundary", {}).get("result", {})
            log.warning("isolated worker self-check did not pass: %s",
                        (report.get("stdout") or "")[-2000:])
            sup.stop()
        return _supervisor


def _describe(report: dict) -> str:
    boundary = report.get("boundary", {}) or {}
    handshake = (boundary.get("result", {}) or {}).get("handshake", {}) or {}
    return (f"backend={backend()} landlock_abi="
            f"{(handshake.get('landlock') or {}).get('abi')} "
            f"ns={(handshake.get('namespaces') or {}).get('pid', '')[:20]}")


def status() -> dict:
    """Operator-facing status snapshot (never includes secrets)."""
    with _lock:
        return {
            "enabled": _enabled(),
            "backend": backend(),
            "verified": bool(_state.get("verified")),
            "checked_at": _state.get("checked_at") or 0,
            "error": _state.get("error", ""),
            "report": _state.get("report", {}),
        }


def executor():
    """Return the job executor used by agents, or ``None`` when blocked."""
    sup = get_supervisor()
    if sup is None:
        return None
    from execution.worker.client import WorkerClient
    return WorkerClient(sup)


def shutdown() -> None:
    global _supervisor
    with _lock:
        if _supervisor is not None:
            _supervisor.stop()
            _supervisor = None
        _state["verified"] = False


def reset_cache_for_tests() -> None:
    """Test helper: forget cached verification without touching the host."""
    global _state
    with _lock:
        _state = {"checked_at": 0.0, "verified": False, "report": {}, "error": "reset"}

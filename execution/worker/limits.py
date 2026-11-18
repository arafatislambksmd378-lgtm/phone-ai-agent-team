"""Process resource limits applied inside the sandboxed child.

These are *hard* kernel limits (RLIMIT_*), not advisory accounting: exceeding
RLIMIT_AS makes ``mmap``/``brk`` fail with ENOMEM, RLIMIT_CPU delivers SIGXCPU
then SIGKILL, RLIMIT_NPROC blocks ``fork``/``clone`` and RLIMIT_FSIZE kills the
process with SIGXFSZ. They complement the supervisor's wall-clock watchdog.

The limits are deliberately applied *in the sandboxed process itself* (after
privileges have been dropped) so a compromised supervisor-visible surface can
never be used to raise them again: only the parent of a process may increase a
hard limit, and the sandboxed process has no parent relationship to the
supervisor after ``execve``.
"""
from __future__ import annotations

import resource

#: Sentinel used by :func:`apply_limits` when a value is explicitly disabled.
UNLIMITED = 0


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def apply_limits(*, cpu_seconds: int, address_space_mb: int, process_count: int,
                 file_size_mb: int, open_files: int = 256,
                 stack_mb: int = 8) -> dict[str, tuple[int, int]]:
    """Apply soft+hard rlimits and return what was actually set.

    Values are clamped to defensible floors so that a malformed job request can
    never disable containment (e.g. ``address_space_mb=0`` would otherwise mean
    "unlimited" to ``setrlimit``, which is the opposite of what a caller wants).
    """
    cpu = clamp(cpu_seconds, 1, 6 * 3600)
    as_mb = clamp(address_space_mb, 64, 65536)
    nproc = clamp(process_count, 8, 4096)
    fsize = clamp(file_size_mb, 1, 4096)
    nofiles = clamp(open_files, 16, 4096)
    stack = clamp(stack_mb, 1, 256)

    applied: dict[str, tuple[int, int]] = {}

    def _set(which: int, soft: int, hard: int, name: str) -> None:
        resource.setrlimit(which, (soft, hard))
        applied[name] = (soft, hard)

    # CPU seconds: soft -> SIGXCPU, hard -> SIGKILL.
    _set(resource.RLIMIT_CPU, cpu, cpu + 5, "cpu")
    _set(resource.RLIMIT_AS, as_mb * 1024 * 1024, as_mb * 1024 * 1024, "as")
    _set(resource.RLIMIT_DATA, as_mb * 1024 * 1024, as_mb * 1024 * 1024, "data")
    _set(resource.RLIMIT_NPROC, nproc, nproc, "nproc")
    _set(resource.RLIMIT_FSIZE, fsize * 1024 * 1024, fsize * 1024 * 1024, "fsize")
    _set(resource.RLIMIT_NOFILE, nofiles, nofiles, "nofile")
    _set(resource.RLIMIT_CORE, 0, 0, "core")
    try:
        _set(resource.RLIMIT_STACK, stack * 1024 * 1024, stack * 1024 * 1024, "stack")
    except (ValueError, OSError):
        # RLIMIT_STACK cannot be <rlimit_cur when a hard limit is required by
        # some kernels; it is defence-in-depth only, never a security boundary.
        pass
    return applied


def read_limits() -> dict[str, list[int]]:
    """Return the current process' rlimits (used by self-verification)."""
    out: dict[str, list[int]] = {}
    for name in ("CPU", "AS", "DATA", "NPROC", "FSIZE", "NOFILE", "CORE", "STACK"):
        which = getattr(resource, f"RLIMIT_{name}", None)
        if which is None:
            continue
        try:
            soft, hard = resource.getrlimit(which)
        except (ValueError, OSError):
            continue
        out[name.lower()] = [int(soft), int(hard)]
    return out

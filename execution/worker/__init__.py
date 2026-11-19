"""Isolated execution worker subsystem (Phase 5.1).

Three independently testable pieces:

* ``rootfs``/``landlock``/``limits`` — kernel primitives used to confine a job.
* ``harness`` — a self-contained, stdlib-only program that applies those
  primitives to itself and then ``execve``-s the requested payload, so the
  parent observes the payload's real exit status.
* ``jobserver`` — the only component that ever executes untrusted code inside a
  namespace; it is spawned exclusively by ``supervisor``.
* ``supervisor`` / ``client`` — the trusted side: admission control, watchdog,
  resource sampling, output capture and guaranteed cleanup.

``execution.isolation`` decides whether a verified backend exists; nothing in
this package is allowed to imply availability on its own.
"""
from __future__ import annotations

__all__ = [
    "limits",
    "rootfs",
    "landlock",
    "harness",
    "jobserver",
    "supervisor",
    "client",
]

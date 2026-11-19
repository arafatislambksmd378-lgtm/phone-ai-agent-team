"""Namespace setup for a single job — the process that isolates *itself*.

Why a separate program
----------------------
``clone(CLONE_NEWPID)`` only affects *children* of the calling process, and
``setuid``/``chroot``/Landlock are irreversible for the caller. The supervisor
must stay unconfined so it can time out, kill and clean up the job. The split is
therefore:

``supervisor`` --spawn--> ``launcher`` (this file) --unshare/clone--> ``harness``

The launcher performs the namespace and credential transitions and then runs the
harness, which performs chroot/Landlock/limits/seccomp and ``execve``s the
payload. Between the launcher and the harness there is **no code that executes as
the service user inside the new mount namespace** — the harness is already
confined by Landlock before it does anything else, and it is executed with the
dropped uid.

User namespace policy
---------------------
The launcher first tries ``CLONE_NEWUSER`` so uid 0 *inside* the namespace is
unprivileged outside it. If user namespaces are unavailable (``EPERM`` from
hardened kernels or container seccomp policies), it falls back to a *real*
``setuid/setgid`` to the pre-created, otherwise unused ``sandbox_uid``. In both
cases the payload ends up unable to escalate. The handshake reports which mode
was used so the supervisor can assert on it.

Failures are always fail-closed: if the launcher cannot establish a namespace it
reports and exits non-zero; it never runs the payload outside a namespace.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys

CLONE_NEWNS = 0x00020000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUTS = 0x04000000
MS_REC = 16384
MS_PRIVATE = 1 << 18


def _fail(stage: str, exc: BaseException) -> "None":
    sys.stderr.write(f"launcher: {stage}: {type(exc).__name__}: {exc}\n")
    sys.stderr.flush()
    sys.exit(71)


def _write_file(path: str, content: str) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        os.write(fd, content.encode("ascii"))
    finally:
        os.close(fd)


def _pidns_usable(libc, flags_with_pidns: int) -> bool:
    """Empirically decide whether a PID namespace can be used on this host.

    Some sandboxed kernels accept ``CLONE_NEWPID`` but then fail every subsequent
    ``fork`` with ENOMEM, which would break any legitimate test payload. Rather
    than assume, the launcher forks a throw-away probe that unshares *all* the
    namespaces (including PID) and then tries to spawn one trivial process; only
    if that succeeds is the PID namespace used for the real job. When it is not
    usable the job still runs inside user/mount/net/ipc/uts namespaces and the
    seccomp policy blocks cross-process signalling instead (reported in the
    handshake as ``pidns: false``).
    """
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        return False
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            os.close(read_fd)
            os.set_inheritable(write_fd, True)
            rc = libc.unshare(flags_with_pidns)
            ok = False
            if rc == 0:
                if flags_with_pidns & CLONE_NEWUSER:
                    outer_uid, outer_gid = spec_uid_pair()
                    _setup_userns(libc, outer_uid, outer_gid)
                # Several sequential forks plus a fork+exec are required: some
                # kernels accept the first fork inside a new PID namespace and
                # then fail every later clone with ENOMEM, which silently breaks
                # any payload that spawn helpers (pytest, git wrappers, linters).
                ok = True
                for _ in range(3):
                    child = os.fork()
                    if child == 0:
                        os._exit(0)
                    _, status = os.waitpid(child, 0)
                    if os.waitstatus_to_exitcode(status) != 0:
                        ok = False
                        break
                if ok:
                    try:
                        probe = os.fork()
                    except OSError:
                        ok = False
                    else:
                        if probe == 0:  # pragma: no cover - child
                            try:
                                os.execve("/usr/bin/true", ["/usr/bin/true"],
                                          {"PATH": "/usr/bin:/bin"})
                            except Exception:
                                os._exit(1)
                        _, status = os.waitpid(probe, 0)
                        ok = ok and os.waitstatus_to_exitcode(status) == 0
            os.write(write_fd, b"1" if ok else b"0")
        except Exception:  # noqa: BLE001 - probe must never raise
            try:
                os.write(write_fd, b"0")
            except OSError:
                pass
        finally:
            os._exit(0)
    os.close(write_fd)
    data = b""
    try:
        while True:
            chunk = os.read(read_fd, 8)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(read_fd)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
    return data == b"1"


_spec_uid_gid: tuple[int, int] = (0, 0)


def spec_uid_pair() -> tuple[int, int]:
    """The uid/gid captured before any namespace transition."""
    return _spec_uid_gid


def _unshare(libc, flags: int) -> None:
    if libc.unshare(flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"unshare(0x{flags:x}): {os.strerror(err)}")


def _setup_userns(libc, outer_uid: int, outer_gid: int) -> None:
    """Write the id maps.

    ``outer_uid``/``outer_gid`` MUST be captured *before* ``unshare``: after a
    user-namespace unshare the process reports the overflow uid (65534) outside
    the namespace, and mapping ``0 65534 1`` is rejected with EPERM.
    """
    try:
        _write_file("/proc/self/setgroups", "deny")
    except OSError:
        pass  # older kernels / already set
    _write_file("/proc/self/uid_map", f"0 {outer_uid} 1\n")
    _write_file("/proc/self/gid_map", f"0 {outer_gid} 1\n")


def main(argv: list[str]) -> None:
    try:
        spec = json.loads(argv[1])
    except (IndexError, json.JSONDecodeError) as exc:
        _fail("spec", exc)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    if os.environ.get("WORKER_DEBUG"):
        sys.stderr.write(f"launcher: debug uid={os.getuid()} euid={os.geteuid()} "
                         f"exe={sys.executable} cwd={os.getcwd()}\n")
    sandbox_uid = int(spec["sandbox_uid"])
    sandbox_gid = int(spec["sandbox_gid"])
    harness = spec["harness"]
    harness_spec = spec["harness_spec"]

    global _spec_uid_gid
    namespace_flags = CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWIPC | CLONE_NEWUTS
    userns_used = False
    outer_uid, outer_gid = os.getuid(), os.getgid()
    _spec_uid_gid = (outer_uid, outer_gid)
    pidns_used = True
    if not _pidns_usable(libc, (CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWIPC
                                | CLONE_NEWUTS | CLONE_NEWUSER)):
        pidns_used = False
        namespace_flags &= ~CLONE_NEWPID
        # Without a PID namespace the payload shares the host's PID space, so it
        # must not be able to signal other processes; harness installs the
        # signal-blocking seccomp policy.
        harness_spec["block_signals"] = True
        if os.environ.get("WORKER_DEBUG"):
            sys.stderr.write("launcher: PID namespace unusable on this host; "
                             "using signal-blocking seccomp policy instead\n")
    harness_spec["pidns"] = pidns_used
    try:
        _unshare(libc, namespace_flags | CLONE_NEWUSER)
        _setup_userns(libc, outer_uid, outer_gid)
        userns_used = True
    except OSError as userns_exc:
        if os.environ.get("WORKER_DEBUG"):
            sys.stderr.write(f"launcher: userns path failed: {userns_exc}\n")
        # User namespaces unavailable: continue with the remaining namespaces and
        # fall back to real uid/gid dropping (see module docstring).
        try:
            _unshare(libc, namespace_flags)
        except OSError as exc:
            _fail("unshare", exc)
            return

    # Make the mount namespace private so nothing done here can propagate to the
    # service host's mount table.
    try:
        if libc.mount(None, b"/", None, MS_REC | MS_PRIVATE, None) != 0:
            err = ctypes.get_errno()
            # Not fatal on its own: the new mount namespace is already private by
            # default when CLONE_NEWNS succeeds without MS_SHARED propagation.
            if err not in (0, 1):
                sys.stderr.write(f"launcher: mount(private) failed: {os.strerror(err)}\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"launcher: mount(private) exception: {exc}\n")

    # Credentials: after a user namespace, uid 0 inside is already unprivileged
    # outside it; otherwise drop for real (irreversible once all caps are gone).
    if not userns_used:
        try:
            os.setgroups([])
            os.setgid(sandbox_gid)
            os.setuid(sandbox_uid)
        except OSError as exc:
            _fail("setuid", exc)
            return

    try:
        os.chdir(spec.get("root") or "/")
    except OSError as exc:
        _fail("chdir", exc)
        return

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": "/workspace",
        "TMPDIR": "/tmp",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "SANDBOX_USERNS": "1" if userns_used else "0",
    }
    argv_out = [sys.executable, harness, json.dumps(harness_spec)]
    try:
        os.execve(sys.executable, argv_out, env)
    except OSError as exc:
        _fail("execve", exc)


if __name__ == "__main__":
    main(sys.argv)

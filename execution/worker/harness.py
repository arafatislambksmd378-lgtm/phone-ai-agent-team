"""Self-confining sandbox harness — the program that *becomes* the sandbox.

The supervisor creates the namespaces and the disposable root, but it must not
drop privileges or install Landlock/seccomp on itself: those are irreversible and
would cripple the watchdog. The supervisor therefore launches this file inside
the new namespaces; the harness applies every confinement step to its own
process, writes a short JSON handshake to stdout, and then ``execve``-s the job
payload so that the supervisor observes the payload's real exit status and the
payload can never regain the harness' Python state.

Handshake
---------
Line 1 of stdout is ``@@PHONE_AGENT_SANDBOX@@ <json>`` containing the verified
state (namespace ids, no_new_privs, landlock ABI, rlimits, seccomp count). The
supervisor parses and *asserts* it; a missing or malformed handshake is treated
as a containment failure and the job is rejected. All payload output after that
line is untrusted.

Ordering matters and is security relevant:

1. ``chdir`` into the root *before* Landlock so path resolution cannot be used
   to reach outside it.
2. Landlock policy (deny-by-default) — everything afterwards is confined.
3. chroot + chdir to the workspace.
4. rlimits.
5. seccomp filter.
6. ``execve``.
"""
from __future__ import annotations

import ctypes
import json
import os
import stat
import sys
import traceback

HANDSHAKE_PREFIX = "@@PHONE_AGENT_SANDBOX@@"

# These imports must work from the *host* interpreter path passed to execve.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _emit(obj: dict) -> None:
    sys.stdout.write(HANDSHAKE_PREFIX + " " + json.dumps(obj, sort_keys=True) + "\n")
    sys.stdout.flush()


def _fail(stage: str, exc: BaseException) -> "None":
    _emit({"status": "error", "stage": stage,
           "error": f"{type(exc).__name__}: {exc}",
           "detail": traceback.format_exc(limit=3)[-1200:]})
    sys.exit(70)


def main(argv: list[str]) -> None:
    try:
        spec = json.loads(argv[1])
    except (IndexError, json.JSONDecodeError) as exc:
        _fail("spec", exc)
        return

    root = spec["root"]
    workspace = spec.get("workspace") or "/workspace"
    # Allowlist entries are sandbox-absolute ("/workspace"); Landlock rules must
    # be created with *host* paths because the policy is installed before chroot.
    def host_path(p: str) -> str:
        if not p or p == "/":
            return root
        return os.path.join(root, str(p).lstrip("/"))

    read_only = [host_path(p) for p in (spec.get("read_only") or [])]
    read_exec = [host_path(p) for p in (spec.get("read_execute") or [])]
    writable = [host_path(p) for p in (spec.get("writable") or [])]

    libc = ctypes.CDLL(None, use_errno=True)

    # 1. Move into the new root before Landlock so relative resolution cannot
    #    escape, and keep a descriptor of the host /proc for namespace identity
    #    readback after the chroot (read-only, closed before exec).
    try:
        proc_fd = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        proc_fd = -1
    try:
        os.chdir(root)
    except OSError as exc:
        _fail("chdir_root", exc)
        return

    # Resolve helper modules BEFORE chroot: ``sys.path`` entries are host paths and
    # will not exist inside the disposable root.
    sys.path.insert(0, _HERE)
    import landlock  # noqa: E402
    import limits as limits_mod  # noqa: E402
    import seccomp  # noqa: E402

    # 2. Landlock (deny-by-default over the whole filesystem).
    landlock_state: dict = {"available": False, "abi": 0}
    try:
        if landlock.available():
            rs = landlock.Ruleset()
            allowed, writable_ok = [], []
            for path in read_only:
                if rs.allow_path(path, landlock.READ_EXEC):
                    allowed.append(path)
            for path in read_exec:
                if rs.allow_path(path, landlock.READ_EXEC):
                    allowed.append(path)
            for path in writable:
                rights = (landlock.READ_EXEC | landlock.FS_WRITE_FILE | landlock.FS_MAKE_REG
                          | landlock.FS_MAKE_DIR | landlock.FS_REMOVE_FILE
                          | landlock.FS_REMOVE_DIR | landlock.FS_MAKE_SYM
                          | landlock.FS_TRUNCATE | (landlock.FS_REFER if rs.abi >= 2 else 0))
                if rs.allow_path(path, rights):
                    writable_ok.append(path)
            rs.apply()
            landlock_state = {"available": True, "abi": rs.abi,
                              "read_execute": allowed, "writable": writable_ok}
    except Exception as exc:  # noqa: BLE001 - any failure must be reported, not hidden
        _fail("landlock", exc)
        return

    # 2b. /dev/null placeholders. Without ``mknod`` permission a regular empty
    # file stands in for the character device: it must be *writable* or every
    # subprocess that redirects to it fails (CPython flushes stderr to
    # /dev/null in places). The job's tmpfs is size-clamped, so a run that
    # writes unboundedly still trips its disk limit instead of filling the host.
    deferred = []
    for name in ("null", "zero", "full", "random", "urandom"):
        dev_path = os.path.join(root, "dev", name)
        try:
            if os.path.exists(dev_path) and not stat.S_ISCHR(os.lstat(dev_path).st_mode):
                os.chmod(dev_path, 0o666)
            elif not os.path.exists(dev_path):
                _touch_mode(dev_path, 0o666)
        except OSError:
            deferred.append(dev_path)

    # 3. chroot into the disposable root.
    try:
        if libc.chroot(root.encode()) != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"chroot({root}): {os.strerror(err)}")
        os.chdir(workspace)
    except Exception as exc:  # noqa: BLE001
        _fail("chroot", exc)
        return

    # 4. Hard resource limits.
    limits_state: dict = {}
    try:
        limits_state = {k: list(v) for k, v in limits_mod.read_limits().items()}
        limits_mod.apply_limits(
            cpu_seconds=int(spec.get("cpu_seconds") or 60),
            address_space_mb=int(spec.get("address_space_mb") or 512),
            process_count=int(spec.get("process_count") or 64),
            file_size_mb=int(spec.get("file_size_mb") or 16),
            open_files=int(spec.get("open_files") or 256),
        )
        limits_state = {k: list(v) for k, v in limits_mod.read_limits().items()}
    except Exception as exc:  # noqa: BLE001
        _fail("limits", exc)
        return

    # 5. Capability bounding set: remove everything.
    caps_dropped = 0
    PR_CAPBSET_DROP = 24
    for cap in range(64):
        if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) == 0:
            caps_dropped += 1

    # 6. seccomp deny filter (never fatal for ordinary runtimes).
    seccomp_count = 0
    try:
        seccomp_count = seccomp.install(block_signals=bool(spec.get("block_signals")))
    except Exception as exc:  # noqa: BLE001
        _fail("seccomp", exc)
        return

    # 7. Handshake + exec.
    # Namespace identities must be read back from the NSFS magic link, which the
    # disposable root cannot provide (no /proc yet), so read them through the
    # still-open descriptor of "/proc" captured before the chroot.
    ns = _namespace_ids(proc_fd)
    _emit({
        "status": "ok",
        "pid": os.getpid(),
        "uid": os.getuid(),
        "gid": os.getgid(),
        "euid": os.geteuid(),
        "egid": os.getegid(),
        "cwd": os.getcwd(),
        "namespaces": ns,
        "landlock": landlock_state,
        "limits": limits_state,
        "capabilities_dropped": caps_dropped,
        "seccomp_rules": seccomp_count,
        "pidns": bool(spec.get("pidns", True)),
        "block_signals": bool(spec.get("block_signals")),
        "no_new_privs": _no_new_privs(libc),
    })

    cmd = [str(c) for c in spec.get("argv") or []]
    if not cmd:
        print("harness: no argv supplied", file=sys.stderr)
        os._exit(64)
    env = {str(k): str(v) for k, v in (spec.get("env") or {}).items()}
    # ``execve`` does not search PATH, and the chroot is in place by now, so
    # resolve relative to the *sandbox* filesystem the payload actually sees.
    cmd[0] = _resolve_program(cmd[0], env.get("PATH", ""))
    if cmd[0] is None:
        print(f"harness: program not found on PATH: {spec.get('argv')[0]!r}", file=sys.stderr)
        os._exit(127)
    try:
        os.execve(cmd[0], cmd, env)
    except Exception as exc:  # noqa: BLE001
        print(f"harness: execve({cmd[0]}) failed: {exc}", file=sys.stderr)
        os._exit(127)


def _resolve_program(program: str, search_path: str) -> "str | None":
    """Resolve ``program`` the way execvp would, inside the current root."""
    if not program:
        return None
    if "/" in program:
        return program if os.path.isfile(program) else None
    for entry in (search_path or "/usr/local/bin:/usr/bin:/bin").split(":"):
        if not entry:
            continue
        candidate = os.path.join(entry, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    # Standard fallbacks, in case PATH was not supplied by the caller.
    for entry in ("/usr/local/bin", "/usr/bin", "/bin"):
        candidate = os.path.join(entry, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _namespace_ids(proc_fd: int) -> dict:
    out: dict[str, str] = {}
    for label in ("pid", "net", "user", "mnt", "ipc", "uts"):
        value = ""
        if proc_fd >= 0:
            try:
                value = os.readlink(f"self/ns/{label}", dir_fd=proc_fd)
            except (OSError, AttributeError):
                value = ""
        out[label] = value
    if proc_fd >= 0:
        try:
            os.close(proc_fd)
        except OSError:
            pass
    return out


def _no_new_privs(libc) -> bool:
    PR_GET_NO_NEW_PRIVS = 39
    return libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1


if __name__ == "__main__":
    main(sys.argv)

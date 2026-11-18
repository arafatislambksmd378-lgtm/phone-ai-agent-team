"""Landlock LSM filesystem-confusion boundary (Linux >= 5.13, ABI 1..5).

Landlock is an *unprivileged, inheritable, kernel-enforced* access-control
mechanism: after ``restrict_self`` the calling process — and every descendant it
spawns — can only access paths that are explicitly allowed. Rules are inherited
across ``execve`` and ``fork``, cannot be removed by the process itself, and are
honoured by the kernel regardless of file permissions or credentials.

This module therefore provides a second, independent boundary behind the
``chroot``: even if the root were mis-built, or a path were re-opened through a
file descriptor, the payload cannot reach:

* the service host's filesystem outside the allowlist,
* the job's own ``/usr``/``/lib`` trees for writing,
* ``/proc/self/environ`` or any other host-provided file.

ABI notes (differences are handled, never assumed):

* ABI 1 handles the file/dir access rights.
* ABI 2 adds ``REFER`` (``rename``/``link`` across directories).
* ABI 3 adds ``TRUNCATE``.
* ABI 4 adds the TCP bind/connect scopes (network is additionally blocked by an
  unshared network namespace, so this is defence in depth).
* ABI 5 adds ``IOCTL_DEV``.

``available()`` only reports a version when the kernel actually answers the
``LANDLOCK_CREATE_RULESET_VERSION`` query, so a container with Landlock compiled
out correctly reports ``0`` instead of failing later.
"""
from __future__ import annotations

import ctypes
import errno
import os

# Syscall numbers are identical on x86_64 and aarch64 for the Landlock family.
_SYS_CREATE = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT = 446

_RULE_PATH_BENEATH = 1

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13        # ABI 2
FS_TRUNCATE = 1 << 14     # ABI 3
FS_IOCTL_DEV = 1 << 15    # ABI 5

ALL_FS_RIGHTS_ABI1_3 = (
    FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_READ_DIR | FS_REMOVE_DIR
    | FS_REMOVE_FILE | FS_MAKE_CHAR | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK
    | FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM | FS_REFER | FS_TRUNCATE
)

NET_BIND_TCP = 1 << 0   # ABI 4
NET_CONNECT_TCP = 1 << 1


class _RulesetAttr(ctypes.Structure):
    """struct landlock_ruleset_attr — only the fields the ABI supports are read."""

    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


_libc = ctypes.CDLL(None, use_errno=True)


def _syscall(*args) -> int:
    return int(_libc.syscall(*args))


def abi_version() -> int:
    """Return the kernel Landlock ABI version, or 0 when unsupported."""
    try:
        version = _syscall(_SYS_CREATE, 0, 0, 1)  # LANDLOCK_CREATE_RULESET_VERSION
    except (OSError, AttributeError):
        return 0
    if version < 0:
        return 0
    return int(version)


def available() -> bool:
    return abi_version() >= 1


def _open_path(path: str) -> int:
    flags = os.O_PATH | os.O_CLOEXEC
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags | nofollow)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            # Symlink: the target is allowed explicitly elsewhere, and a
            # symlink cannot be used as a ruleset root, so skip it.
            raise
        raise


class Ruleset:
    """A Landlock ruleset builder; ``apply`` is irreversible for the process."""

    def __init__(self) -> None:
        self.abi = abi_version()
        if self.abi < 1:
            raise OSError(errno.ENOTSUP, "Landlock is not available on this kernel")
        handled = ALL_FS_RIGHTS_ABI1_3
        if self.abi < 2:
            handled &= ~FS_REFER
        if self.abi < 3:
            handled &= ~FS_TRUNCATE
        if self.abi >= 5:
            handled |= FS_IOCTL_DEV
        self.handled_fs = handled
        attr = _RulesetAttr(handled, 0, 0)
        size = 8 if self.abi < 4 else 24
        fd = _syscall(_SYS_CREATE, ctypes.byref(attr), size, 0)
        if fd < 0:
            err = ctypes.get_errno()
            # ENOMSG/EOPNOTSUPP: Landlock compiled out or disabled by LSM config.
            raise OSError(err, f"landlock_create_ruleset failed: {os.strerror(err)}")
        self.fd = fd
        self._closed = False

    # -- rules -------------------------------------------------------------
    def allow_path(self, path: str, rights: int | None = None) -> bool:
        """Allow ``rights`` beneath ``path`` (missing paths are skipped)."""
        if self._closed:
            raise RuntimeError("ruleset already applied")
        rights = self.handled_fs if rights is None else (rights & self.handled_fs)
        if not os.path.exists(path):
            return False
        # A rule attached to a *file* may only carry file-level rights; the
        # kernel rejects directory-only bits (MAKE_*, REMOVE_*, READ_DIR) with
        # EINVAL and the whole rule is silently lost.
        try:
            is_dir = os.path.isdir(path) and not os.path.islink(path)
        except OSError:
            return False
        if not is_dir:
            rights &= (FS_EXECUTE | FS_READ_FILE | FS_WRITE_FILE | FS_TRUNCATE | FS_IOCTL_DEV)
        if not rights:
            return False
        try:
            dir_fd = _open_path(path)
        except OSError:
            return False
        try:
            rule = _PathBeneathAttr(rights, dir_fd)
            rc = _syscall(_SYS_ADD_RULE, self.fd, _RULE_PATH_BENEATH,
                          ctypes.byref(rule), 0)
            return rc == 0
        finally:
            os.close(dir_fd)

    def apply(self) -> None:
        """Restrict the current process (and all future children) — irreversible."""
        if self._closed:
            raise RuntimeError("ruleset already applied")
        # Landlock requires no_new_privs unless the caller has CAP_SYS_ADMIN.
        if _libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            err = ctypes.get_errno()
            raise OSError(err, f"prctl(PR_SET_NO_NEW_PRIVS) failed: {os.strerror(err)}")
        rc = _syscall(_SYS_RESTRICT, self.fd, 0)
        os.close(self.fd)
        self._closed = True
        if rc != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"landlock_restrict_self failed: {os.strerror(err)}")

    def __enter__(self) -> "Ruleset":
        return self

    def __exit__(self, *exc) -> None:
        if not self._closed:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self._closed = True


# -- convenience policy -----------------------------------------------------
READ_EXEC = FS_READ_FILE | FS_READ_DIR | FS_EXECUTE
READ_WRITE = READ_EXEC | FS_WRITE_FILE | FS_TRUNCATE


def install_policy(root: str, *, writable_paths: list[str] | None = None,
                   extra_read_paths: list[str] | None = None) -> dict:
    """Install a deny-by-default policy rooted at ``root``.

    * everything under ``root`` is read+execute (the runtime),
    * ``writable_paths`` additionally get write/create/remove rights,
    * ``extra_read_paths`` are opened read-only from outside the root (used only
      for the interpreter's shared libraries when they resolve through
      absolute host paths).
    """
    rs = Ruleset()
    allowed: list[str] = []
    for candidate in (root, *[p for p in (extra_read_paths or []) if p]):
        if rs.allow_path(candidate, READ_EXEC):
            allowed.append(candidate)
    writable: list[str] = []
    make_rights = (
        FS_WRITE_FILE | FS_MAKE_REG | FS_MAKE_DIR | FS_REMOVE_FILE | FS_REMOVE_DIR
        | FS_MAKE_SYM | FS_TRUNCATE | (FS_REFER if rs.abi >= 2 else 0)
    )
    for path in (writable_paths or []):
        if rs.allow_path(path, READ_EXEC | make_rights):
            writable.append(path)
    rs.apply()
    return {
        "abi": rs.abi,
        "read_execute": allowed,
        "writable": writable,
    }

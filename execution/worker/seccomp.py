"""Seccomp-BPF filter assembly for the sandbox harness.

Scope and honesty
-----------------
Seccomp is *additional* hardening on top of the real boundaries (chroot,
Landlock, user/mount/net namespaces, real uid drop). It is a deny-list, not an
allow-list: an allow-list that keeps CPython plus its C extensions working would
have to permit most of the syscall surface anyway, while a too-strict list would
silently break ordinary test runs. The denied set is the dangerous kernel
surface a build/test payload never legitimately needs (namespace manipulation,
mount, ptrace, kernel modules, keyrings, io_uring, BPF, file handles).

Signal policy
-------------
When the host cannot provide a PID namespace (some CI sandboxes accept
``CLONE_NEWPID`` but then fail every subsequent ``fork`` with ENOMEM), the
payload shares the host PID space and could otherwise signal the service process
running under the same uid. In that mode the filter denies the signal syscalls
*unless* the target is the payload's own process id or process group (``0``).
Self-signalling is preserved so runtimes that probe signals keep working, and
``subprocess``/``waitpid`` are unaffected because they do not signal.

Denied calls return ``EPERM`` rather than killing the process so a runtime that
probes a feature degrades gracefully. A filter built for one ABI must never be
evaluated on another, so a foreign ``arch`` kills the process instead.
"""
from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct

# --- BPF program encoding --------------------------------------------------
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_JGE = 0x30
BPF_K = 0x00
BPF_RET = 0x06
BPF_ALU = 0x04
BPF_AND = 0x50

SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000

AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7

_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0 = 16          # struct seccomp_data.args[0] (little-endian low word)
_ARCH_INDEX = {"x86_64": 0, "amd64": 0, "aarch64": 1, "arm64": 1}

#: Unconditional denials — no build or test payload needs these.
_DENY: dict[str, tuple[int | None, int | None]] = {
    "mount": (165, 40),
    "umount2": (166, 39),
    "pivot_root": (155, 41),
    "chroot": (161, 51),
    "unshare": (272, 97),
    "setns": (308, 268),
    "ptrace": (101, 117),
    "process_vm_readv": (310, 270),
    "process_vm_writev": (311, 271),
    "kcmp": (312, 272),
    "bpf": (321, 280),
    "perf_event_open": (298, 241),
    "userfaultfd": (323, 282),
    "add_key": (248, 217),
    "request_key": (249, 218),
    "keyctl": (250, 219),
    "init_module": (175, 105),
    "finit_module": (313, 273),
    "delete_module": (176, 106),
    "kexec_load": (246, 104),
    "reboot": (169, 142),
    "swapon": (167, 224),
    "swapoff": (168, 225),
    "acct": (163, 89),
    "quotactl": (179, 60),
    "syslog": (103, 116),
    "vhangup": (153, 58),
    "iopl": (172, None),
    "ioperm": (173, None),
    "open_by_handle_at": (304, 265),
    "name_to_handle_at": (303, 264),
    "io_uring_setup": (425, 425),
    "io_uring_enter": (426, 426),
    "io_uring_register": (427, 427),
    "process_madvise": (440, 440),
    "pidfd_open": (434, 434),
    "pidfd_getfd": (438, 438),
    "pidfd_send_signal": (424, 424),
}

#: Signal syscalls restricted (not denied) when no PID namespace is available.
_SIGNAL_FAMILY: dict[str, tuple[int | None, int | None]] = {
    "kill": (62, 129),
    "tkill": (200, 130),
    "tgkill": (234, 131),
    "rt_sigqueueinfo": (129, 138),
    "rt_tgsigqueueinfo": (297, 240),
}

#: Syscalls taking the target pid in a different argument position.
_SIGNAL_ARG_INDEX = {"tgkill": 0, "rt_tgsigqueueinfo": 0}


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


def _arch_key(machine: str | None = None) -> str:
    key = (machine or platform.machine()).lower()
    if key not in _ARCH_INDEX:
        raise OSError(f"unsupported architecture for seccomp policy: {key}")
    return key


def denied_syscalls(machine: str | None = None) -> tuple[str, ...]:
    return tuple(sorted(_table(_arch_key(machine))))


def _table(key: str) -> dict[str, int]:
    idx = _ARCH_INDEX[key]
    out: dict[str, int] = {}
    for name, nums in _DENY.items():
        nr = nums[idx]
        if nr is not None:
            out[name] = nr
    return out


def syscall_table(machine: str | None = None, *, block_signals: bool = False) -> dict[str, int]:
    """Return the deny table as ``{name: number}`` for the given machine."""
    key = _arch_key(machine)
    table = _table(key)
    if block_signals:
        idx = _ARCH_INDEX[key]
        for name, nums in _SIGNAL_FAMILY.items():
            if nums[idx] is not None:
                table[name] = nums[idx]  # type: ignore[assignment]
    return table


class _Asm:
    """Minimal label-based assembler for classic BPF."""

    def __init__(self) -> None:
        self.ins: list[list] = []          # [code, jt, jf, k] with symbolic slots
        self.labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        self.labels[name] = len(self.ins)

    def emit(self, code: int, k: int = 0, jt=None, jf=None) -> int:
        """Emit one instruction.

        ``k`` is always the literal (syscall number, arch, errno, offset);
        ``jt``/``jf`` are jump *targets* (label names or absolute indices).
        For non-jump opcodes ``jt``/``jf`` are ignored by the kernel.
        """
        index = len(self.ins)
        self.ins.append([code, jt, jf, int(k)])
        return index

    def resolve(self, index: int, target) -> int:
        if isinstance(target, int):
            return target
        if target not in self.labels:
            raise KeyError(f"unknown label {target!r}")
        return self.labels[target]

    def assemble(self) -> bytes:
        out = []
        for index, (code, jt, jf, k) in enumerate(self.ins):
            # Relative offsets are counted from the *next* instruction.
            jt_v = 0 if jt is None else self.resolve(index, jt) - index - 1
            jf_v = 0 if jf is None else self.resolve(index, jf) - index - 1
            if not (0 <= jt_v <= 255 and 0 <= jf_v <= 255):
                raise ValueError(f"seccomp jump out of range at {index}: jt={jt_v} jf={jf_v}")
            out.append(struct.pack("=HBBI", code, jt_v, jf_v, k & 0xFFFFFFFF))
        return b"".join(out)


def bpf_program(machine: str | None = None, *, block_signals: bool = False,
                self_pid: int = 0) -> bytes:
    """Assemble the filter as raw ``struct sock_filter`` records.

    ``self_pid`` is the pid the payload will have after ``execve`` (the harness'
    pid, which ``execve`` preserves).
    """
    key = _arch_key(machine)
    audit_arch = AUDIT_ARCH_X86_64 if _ARCH_INDEX[key] == 0 else AUDIT_ARCH_AARCH64
    deny = _table(key)
    asm = _Asm()

    asm.emit(BPF_LD | BPF_W | BPF_ABS, _OFF_ARCH)
    asm.emit(BPF_JMP | BPF_JEQ | BPF_K, audit_arch, jt="have_arch")
    asm.emit(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS)
    asm.label("have_arch")
    asm.emit(BPF_LD | BPF_W | BPF_ABS, _OFF_NR)

    # Signal policy. Every jump here is a *jf* (fall-through-on-match) jump:
    # some seccomp verifiers reject forward ``jt`` jumps that cross a RET, so
    # only the proven "match falls through" shape is emitted.
    if block_signals and self_pid > 0:
        idx = _ARCH_INDEX[key]
        for name, nums in _SIGNAL_FAMILY.items():
            nr = nums[idx]
            if nr is None:
                continue
            arg = _SIGNAL_ARG_INDEX.get(name, 0)
            skip = f"skip_{name}"
            allow_self = f"self_{name}"
            allow_grp = f"group_{name}"
            resume = f"resume_{name}"
            asm.emit(BPF_JMP | BPF_JEQ | BPF_K, nr, jt=None, jf=skip)
            asm.emit(BPF_LD | BPF_W | BPF_ABS, _OFF_ARG0 + 8 * arg)
            asm.emit(BPF_JMP | BPF_JEQ | BPF_K, self_pid, jt=None, jf=allow_self)
            asm.emit(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)          # self pid
            asm.label(allow_self)
            asm.emit(BPF_JMP | BPF_JEQ | BPF_K, 0, jt=None, jf=allow_grp)
            asm.emit(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)          # own group
            asm.label(allow_grp)
            asm.emit(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | errno.EPERM)
            asm.label(resume)
            asm.emit(BPF_LD | BPF_W | BPF_ABS, _OFF_NR)            # restore nr
            asm.label(skip)

    # The label must point AT the allow instruction, never past the program.
    asm.label("allow")
    asm.emit(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
    return asm.assemble()


def install(machine: str | None = None, *, block_signals: bool = False) -> int:
    """Install the filter on the calling thread; returns the instruction count."""
    raw = bpf_program(machine, block_signals=block_signals, self_pid=os.getpid())
    count = len(raw) // 8
    array = (_SockFilter * count).from_buffer_copy(raw)
    fprog = _SockFprog(count, array)

    libc = ctypes.CDLL(None, use_errno=True)
    PR_SET_NO_NEW_PRIVS = 38
    PR_SET_SECCOMP = 22
    SECCOMP_MODE_FILTER = 2
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"prctl(PR_SET_NO_NEW_PRIVS): {os.strerror(err)}")
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"prctl(PR_SET_SECCOMP): {os.strerror(err)}")
    return count

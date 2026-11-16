"""Permission policy — command allowlist + approval gates.

Security model (fail-closed):
  * Only programs in SAFE_COMMANDS / APPROVAL_REQUIRED may run.
  * Shell interpreters and inline-eval flags are blocked (no `sh -c`, `python3 -c`).
  * Shell metacharacters are rejected (we never use shell=True, so they are
    pointless and only serve injection).
  * Unknown programs are rejected, not silently approved.
"""
from __future__ import annotations

# Commands allowed without human approval (read-only / safe build tools).
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "echo", "pwd", "which",
    "find", "grep", "du", "df", "stat", "sha256sum",
    "python3", "pytest", "git", "node", "npm",
}

# Commands that ALWAYS require an explicit human approval record.
APPROVAL_REQUIRED = {
    "rm", "mv", "cp", "chmod", "chown", "curl", "wget", "ssh", "scp",
    "docker", "kubectl", "aws", "gcloud", "az", "pip", "pip3", "npm",
}

# Never execute, even with approval.
BLOCKED_PROGRAMS = {
    "sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh",
    "sudo", "su", "doas", "env", "xargs", "eval", "exec",
    "mkfs", "dd", "shutdown", "reboot", "halt", "poweroff", "init",
    "systemctl", "service", "crontab", "nc", "ncat", "telnet", "socat",
}

# Shell metacharacters — we never run through a shell, so any of these is
# either meaningless or an injection attempt.
SHELL_METACHARACTERS = (";", "|", "&", ">", "<", "`", "$(", "${", "\n", "\r", "\x00")

# Inline evaluation flags that turn an allowed interpreter into arbitrary code exec.
INLINE_EVAL_FLAGS = {"python3": {"-c"}, "python": {"-c"}, "node": {"-e", "-p", "--eval", "--print"}}

BLOCKED_PREFIXES = ("rm -rf /", "mkfs", "dd ", ":(){", "shutdown", "reboot", "halt", "poweroff")

PROTECTED_FILENAMES = {".env", "id_rsa", "id_ed25519", ".git/config"}


def _program(cmd: str) -> str:
    return cmd.strip().split()[0].split("/")[-1]


def is_command_allowed(cmd: str) -> tuple[bool, bool]:
    """Return (allowed, needs_approval). Fails closed on anything unusual."""
    c = (cmd or "").strip()
    if not c:
        return False, False
    for b in BLOCKED_PREFIXES:
        if c.startswith(b):
            return False, False
    for meta in SHELL_METACHARACTERS:
        if meta in c:
            return False, False
    prog = _program(c)
    if prog in BLOCKED_PROGRAMS:
        return False, False
    try:
        import shlex
        parts = shlex.split(c)
    except ValueError:
        return False, False
    if not parts:
        return False, False
    flags = set(parts[1:])
    if prog in INLINE_EVAL_FLAGS and (flags & INLINE_EVAL_FLAGS[prog]):
        return False, False
    if prog in APPROVAL_REQUIRED:
        return True, True
    if prog in SAFE_COMMANDS:
        # git push / destructive git and installs always need approval
        if prog == "git" and any(x in parts for x in ("push", "reset", "clean", "remote", "checkout")):
            return True, True
        if prog == "npm" and "install" in parts:
            return True, True
        return True, False
    # Unknown program: reject (allowlist model).
    return False, False


def is_path_safe(path: str, workspace_root: str) -> bool:
    """True only if `path` resolves inside `workspace_root` (no sibling-prefix escape)."""
    import os
    root = os.path.realpath(workspace_root)
    target = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if target == root:
        return True
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False

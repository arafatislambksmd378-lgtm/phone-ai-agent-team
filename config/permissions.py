"""Permission policy — command allowlist + approval gates."""
from __future__ import annotations

# Commands allowed without human approval (read-only / safe).
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "echo", "pwd",
    "python3", "pip", "git", "pytest", "node", "npm",
}

# Commands that ALWAYS require approval.
APPROVAL_REQUIRED = {
    "rm", "mv", "chmod", "chown", "curl", "wget", "ssh", "scp",
    "docker", "kubectl", "aws", "gcloud", "az",
}

# Prefixes blocked outright (never execute).
BLOCKED_PREFIXES = ("rm -rf /", "mkfs", "dd ", ":(){", "shutdown", "reboot", "halt", "poweroff")

# File ops that require approval: writes outside workspace, deletes, overwrites of protected files.
PROTECTED_FILENAMES = {".env", "id_rsa", "id_ed25519", ".git/config"}

def is_command_allowed(cmd: str) -> tuple[bool, bool]:
    """Return (allowed, needs_approval)."""
    c = cmd.strip()
    if not c:
        return False, False
    for b in BLOCKED_PREFIXES:
        if c.startswith(b):
            return False, False
    prog = c.split()[0].split("/")[-1]
    if prog in APPROVAL_REQUIRED:
        return True, True
    if prog in SAFE_COMMANDS:
        # git push / git remote etc. need approval
        if prog == "git" and any(x in c for x in ["push", "remote add", "reset --hard", "clean -"]):
            return True, True
        if prog == "pip" and "install" in c:
            return True, True
        return True, False
    # Unknown programs: require approval, don't auto-run
    return True, True

def is_path_safe(path: str, workspace_root: str) -> bool:
    import os
    root = os.path.abspath(workspace_root)
    target = os.path.abspath(os.path.join(root, path)) if not os.path.isabs(path) else os.path.abspath(path)
    return target == root or target.startswith(root + os.sep)

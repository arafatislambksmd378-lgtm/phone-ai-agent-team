"""Approval workflow — execution-enforced human gates, TTL expiration, and audit logging."""
from __future__ import annotations

import time
from memory.database import Database

# High-impact actions that ALWAYS require explicit human approval at the execution layer.
DANGEROUS_ACTIONS = {
    "git_push",
    "public_github_release",
    "deploy",
    "production_deploy",
    "publish",
    "delete_files",
    "destructive_migration",
    "database_delete",
    "modify_outside_workspace",
    "install_packages",
    "spend_money",
    "paid_api_usage",
    "change_external_account",
    "secret_operation",
    "expose_credentials",
    "destructive_command",
    "external_build",
}


class ApprovalRequiredError(PermissionError):
    """Raised when an execution path attempts a gated action without verified approval."""

    def __init__(self, message: str, approval_id: str = "", action: str = ""):
        super().__init__(message)
        self.approval_id = approval_id
        self.action = action


class ApprovalManager:
    def __init__(self, db: Database, default_ttl_seconds: int = 3600, notify_callback=None):
        self.db = db
        self.default_ttl_seconds = max(60, int(default_ttl_seconds or 3600))
        self._notify_callback = notify_callback

    def set_notify_callback(self, fn) -> None:
        """Optional callback(approval_row_dict) invoked when a new approval is created."""
        self._notify_callback = fn

    def request(self, project_id: str, action: str, detail: str,
                task_id: str | None = None, ttl_seconds: int | None = None) -> str:
        ttl = self.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        aid = self.db.create_approval(project_id, action, detail, task_id=task_id, ttl_seconds=ttl)
        if self._notify_callback is not None:
            row = self.db.get_approval(aid)
            if row:
                try:
                    self._notify_callback(row)
                except Exception:
                    pass
        return aid

    def approve(self, approval_id: str, by: str = "") -> bool:
        return self.db.decide_approval(approval_id, True, by)

    def reject(self, approval_id: str, by: str = "") -> bool:
        return self.db.decide_approval(approval_id, False, by)

    def pending(self, project_id: str | None = None) -> list[dict]:
        return self.db.pending_approvals(project_id)

    def get(self, approval_id: str) -> dict | None:
        return self.db.get_approval(approval_id)

    def is_approved(self, approval_id: str) -> bool:
        row = self.db.get_approval(approval_id)
        if not row or row["status"] != "APPROVED":
            return False
        if row.get("expires_at") and row["expires_at"] <= time.time():
            return False
        return True

    def is_rejected(self, approval_id: str) -> bool:
        row = self.db.get_approval(approval_id)
        return bool(row and row["status"] == "REJECTED")

    def is_expired(self, approval_id: str) -> bool:
        row = self.db.get_approval(approval_id)
        return bool(row and row["status"] == "EXPIRED")

    def status(self, approval_id: str) -> str | None:
        row = self.db.get_approval(approval_id)
        return row["status"] if row else None

    def require(self, project_id: str, action: str, detail: str,
                task_id: str | None = None, ttl_seconds: int | None = None) -> str:
        """Create a pending approval for a dangerous action; caller must wait for it."""
        if action not in DANGEROUS_ACTIONS:
            raise ValueError(f"Action {action!r} is not in the dangerous-action policy set")
        return self.request(project_id, action, detail, task_id=task_id, ttl_seconds=ttl_seconds)

    def verify(self, approval_id: str | None, *, project_id: str | None = None,
               action: str | None = None, consume: bool = False,
               actor: str = "execution") -> tuple[bool, str]:
        """Verify an approval record at the execution boundary (never trust booleans)."""
        if not approval_id:
            return False, "Missing approval_id"
        row = self.db.get_approval(approval_id)
        if not row:
            self.db.log_approval_audit(approval_id, project_id or "", "VERIFY_FAILED_MISSING",
                                       actor=actor, detail=action or "")
            return False, f"Unknown approval_id: {approval_id}"
        if row["status"] == "EXPIRED":
            self.db.log_approval_audit(approval_id, row["project_id"], "VERIFY_FAILED_EXPIRED",
                                       actor=actor, detail=action or "")
            return False, f"Approval {approval_id} has expired"
        if row["status"] != "APPROVED":
            self.db.log_approval_audit(approval_id, row["project_id"], "VERIFY_FAILED_STATUS",
                                       actor=actor, detail=f"status={row['status']}")
            return False, f"Approval {approval_id} is {row['status']}, not APPROVED"
        if project_id and row["project_id"] != project_id:
            self.db.log_approval_audit(approval_id, project_id, "VERIFY_FAILED_PROJECT_MISMATCH",
                                       actor=actor, detail=f"expected={project_id} got={row['project_id']}")
            return False, "Approval belongs to a different project"
        if action and row["action"] != action:
            self.db.log_approval_audit(approval_id, row["project_id"], "VERIFY_FAILED_ACTION_MISMATCH",
                                       actor=actor, detail=f"expected={action} got={row['action']}")
            return False, f"Approval action mismatch ({row['action']} != {action})"
        if consume:
            if row.get("consumed_at") is not None:
                self.db.log_approval_audit(approval_id, row["project_id"], "VERIFY_FAILED_ALREADY_CONSUMED",
                                           actor=actor, detail=action or "")
                return False, f"Approval {approval_id} was already consumed"
            self.db.mark_approval_consumed(approval_id, actor=actor)
        else:
            self.db.log_approval_audit(approval_id, row["project_id"], "VERIFIED",
                                       actor=actor, detail=action or row["action"])
        return True, "Approved"

    def record_unauthorized(self, approval_id: str, actor: str, detail: str = "") -> None:
        row = self.db.get_approval(approval_id) if approval_id else None
        pid = row["project_id"] if row else ""
        self.db.log_approval_audit(approval_id or "unknown", pid, "UNAUTHORIZED_ATTEMPT",
                                   actor=str(actor), detail=detail)

    def audit_logs(self, project_id: str | None = None, limit: int = 100) -> list[dict]:
        return self.db.list_approval_audit_logs(project_id, limit=limit)

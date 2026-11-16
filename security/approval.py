"""Approval workflow — human gates for dangerous actions, single-decision semantics."""
from __future__ import annotations

from memory.database import Database

# Actions that always need a human decision.
DANGEROUS_ACTIONS = {
    "git_push", "deploy", "publish", "delete_files", "modify_outside_workspace",
    "install_packages", "spend_money", "change_external_account", "expose_credentials",
    "destructive_command",
}


class ApprovalManager:
    def __init__(self, db: Database):
        self.db = db

    def request(self, project_id: str, action: str, detail: str, task_id: str | None = None) -> str:
        return self.db.create_approval(project_id, action, detail, task_id)

    def approve(self, approval_id: str, by: str = "") -> bool:
        return self.db.decide_approval(approval_id, True, by)

    def reject(self, approval_id: str, by: str = "") -> bool:
        return self.db.decide_approval(approval_id, False, by)

    def pending(self, project_id: str | None = None) -> list[dict]:
        return self.db.pending_approvals(project_id)

    def is_approved(self, approval_id: str) -> bool:
        row = self.db.get_approval(approval_id)
        return bool(row and row["status"] == "APPROVED")

    def is_rejected(self, approval_id: str) -> bool:
        row = self.db.get_approval(approval_id)
        return bool(row and row["status"] == "REJECTED")

    def status(self, approval_id: str) -> str | None:
        row = self.db.get_approval(approval_id)
        return row["status"] if row else None

    def require(self, project_id: str, action: str, detail: str) -> str:
        """Create a pending approval for a dangerous action; caller must wait for it."""
        if action not in DANGEROUS_ACTIONS:
            raise ValueError(f"Action {action!r} is not in the dangerous-action policy set")
        return self.request(project_id, action, detail)

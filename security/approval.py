"""Approval workflow — human gates for dangerous actions."""
from __future__ import annotations
from memory.database import Database

class ApprovalManager:
    def __init__(self, db: Database):
        self.db = db

    def request(self, project_id: str, action: str, detail: str, task_id: str | None = None) -> str:
        return self.db.create_approval(project_id, action, detail, task_id)

    def approve(self, approval_id: str, by: str = "") -> None:
        self.db.decide_approval(approval_id, True, by)

    def reject(self, approval_id: str, by: str = "") -> None:
        self.db.decide_approval(approval_id, False, by)

    def pending(self, project_id: str | None = None) -> list[dict]:
        return self.db.pending_approvals(project_id)

    def is_approved(self, approval_id: str) -> bool:
        row = self.db.query_one("SELECT status FROM approvals WHERE id=?", (approval_id,))
        return bool(row and row["status"] == "APPROVED")

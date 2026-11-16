"""Execution lifecycle state machine — persisted validation, atomic terminal states."""
from __future__ import annotations

STATES = {
    "IDLE", "INPUT_RECEIVED", "VALIDATING", "PLANNING", "QUEUED", "EXECUTING",
    "TESTING", "REVIEWING", "COMPLETED", "PAUSED", "FAILED", "CANCELLED",
    "RECOVERING", "WAITING_FOR_APPROVAL",
}

TRANSITIONS: dict[str, set[str]] = {
    "IDLE": {"INPUT_RECEIVED", "QUEUED"},
    "INPUT_RECEIVED": {"VALIDATING", "FAILED", "CANCELLED"},
    "VALIDATING": {"PLANNING", "FAILED", "WAITING_FOR_APPROVAL", "CANCELLED"},
    "PLANNING": {"QUEUED", "FAILED", "WAITING_FOR_APPROVAL", "CANCELLED"},
    "QUEUED": {"EXECUTING", "PAUSED", "CANCELLED", "FAILED", "RECOVERING", "INPUT_RECEIVED"},
    "EXECUTING": {"QUEUED", "TESTING", "REVIEWING", "PAUSED", "FAILED", "CANCELLED",
                  "WAITING_FOR_APPROVAL", "RECOVERING"},
    "TESTING": {"REVIEWING", "EXECUTING", "FAILED", "PAUSED", "CANCELLED"},
    "REVIEWING": {"COMPLETED", "EXECUTING", "FAILED", "PAUSED", "CANCELLED"},
    "PAUSED": {"QUEUED", "EXECUTING", "CANCELLED", "RECOVERING"},
    "FAILED": {"RECOVERING", "CANCELLED"},
    "RECOVERING": {"QUEUED", "FAILED", "CANCELLED"},
    "WAITING_FOR_APPROVAL": {"QUEUED", "EXECUTING", "CANCELLED", "FAILED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

# States from which a project must never be moved back into work.
TERMINAL = {"COMPLETED", "CANCELLED"}


class LifecycleManager:
    """In-memory view; the DB column `projects.lifecycle` is the source of truth."""

    def __init__(self, db=None):
        self._states: dict[str, str] = {}
        self.db = db

    def get(self, project_id: str) -> str:
        if project_id in self._states:
            return self._states[project_id]
        if self.db is not None:
            row = self.db.query_one("SELECT lifecycle FROM projects WHERE id=?", (project_id,))
            if row and row.get("lifecycle"):
                self._states[project_id] = row["lifecycle"]
                return row["lifecycle"]
        return "IDLE"

    def can_transition(self, from_state: str, to_state: str) -> bool:
        return to_state in TRANSITIONS.get(from_state, set())

    def set(self, project_id: str, state: str) -> bool:
        """Persist a state change. Illegal transitions are refused, not silently applied."""
        if state not in STATES:
            raise ValueError(f"Unknown state: {state}")
        current = self.get(project_id)
        if current == state:
            return True
        if not self.can_transition(current, state):
            return False
        self._states[project_id] = state
        if self.db is not None:
            self.db.set_project_status(project_id, state, lifecycle=state)
        return True

    def transition(self, project_id: str, to_state: str) -> bool:
        return self.set(project_id, to_state)

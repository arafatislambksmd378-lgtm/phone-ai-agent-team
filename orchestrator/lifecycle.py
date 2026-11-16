"""Execution lifecycle state machine."""
from __future__ import annotations

STATES = {
    "IDLE", "INPUT_RECEIVED", "VALIDATING", "PLANNING", "QUEUED", "EXECUTING",
    "TESTING", "REVIEWING", "COMPLETED", "PAUSED", "FAILED", "CANCELLED",
    "RECOVERING", "WAITING_FOR_APPROVAL",
}

# Allowed transitions (None = any, for simplicity we validate membership only)
TRANSITIONS: dict[str, set[str]] = {
    "IDLE": {"INPUT_RECEIVED", "QUEUED"},
    "INPUT_RECEIVED": {"VALIDATING", "FAILED", "CANCELLED"},
    "VALIDATING": {"PLANNING", "FAILED", "WAITING_FOR_APPROVAL"},
    "PLANNING": {"QUEUED", "FAILED", "WAITING_FOR_APPROVAL"},
    "QUEUED": {"EXECUTING", "PAUSED", "CANCELLED"},
    "EXECUTING": {"TESTING", "REVIEWING", "PAUSED", "FAILED", "CANCELLED", "WAITING_FOR_APPROVAL"},
    "TESTING": {"REVIEWING", "FAILED", "PAUSED"},
    "REVIEWING": {"COMPLETED", "FAILED", "PAUSED"},
    "PAUSED": {"QUEUED", "CANCELLED", "RECOVERING"},
    "FAILED": {"RECOVERING", "CANCELLED"},
    "RECOVERING": {"QUEUED", "FAILED", "CANCELLED"},
    "WAITING_FOR_APPROVAL": {"QUEUED", "CANCELLED", "FAILED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

class LifecycleManager:
    def __init__(self):
        self._states: dict[str, str] = {}

    def set(self, project_id: str, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown state: {state}")
        self._states[project_id] = state

    def get(self, project_id: str) -> str:
        return self._states.get(project_id, "IDLE")

    def can_transition(self, from_state: str, to_state: str) -> bool:
        return to_state in TRANSITIONS.get(from_state, set())

    def transition(self, project_id: str, to_state: str) -> bool:
        cur = self.get(project_id)
        if not self.can_transition(cur, to_state):
            return False
        self._states[project_id] = to_state
        return True

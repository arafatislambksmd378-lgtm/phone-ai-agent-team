"""Persistent task queue facade over SQLite."""
from __future__ import annotations
from memory.database import Database

class TaskQueue:
    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, project_id: str, agent_id: str, title: str, payload: dict | None = None, priority: int = 100) -> str:
        return self.db.create_task(project_id, agent_id, title, payload, priority)

    def claim(self, worker_id: str) -> dict | None:
        return self.db.claim_task(worker_id)

    def complete(self, task_id: str, result: str) -> None:
        self.db.set_task_status(task_id, "COMPLETED", result=result)

    def fail(self, task_id: str, error: str) -> None:
        self.db.set_task_status(task_id, "FAILED", error=error)

    def list_for_project(self, project_id: str) -> list[dict]:
        return self.db.list_tasks(project_id)

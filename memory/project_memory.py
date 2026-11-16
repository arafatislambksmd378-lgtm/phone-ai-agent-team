"""Project memory — structured facts persisted per project."""
from __future__ import annotations
from memory.database import Database

class ProjectMemory:
    def __init__(self, db: Database):
        self.db = db

    def remember(self, project_id: str, key: str, value: str) -> None:
        facts = self.db.get_memory(project_id)
        facts[key] = value
        self.db.set_memory(project_id, facts)

    def recall(self, project_id: str) -> dict:
        return self.db.get_memory(project_id)

    def recall_key(self, project_id: str, key: str, default: str = "") -> str:
        return self.db.get_memory(project_id).get(key, default)

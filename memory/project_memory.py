"""Project memory — legacy facts plus versioned, searchable structured entries.

All reads/writes are scoped to a project id. ``facts`` is preserved unchanged for
backward compatibility with the planner and older databases; new structured
knowledge is stored in ``memory_entries`` with source attribution, SHA-256
deduplication, supersession/version history and bounded retention.
"""
from __future__ import annotations

from memory.database import Database


class ProjectMemory:
    def __init__(self, db: Database, max_versions: int = 25):
        self.db = db
        self.max_versions = max(2, min(int(max_versions), 200))

    def remember(self, project_id: str, key: str, value: str) -> None:
        facts = self.db.get_memory(project_id)
        facts[key] = value
        self.db.set_memory(project_id, facts)

    def recall(self, project_id: str) -> dict:
        return self.db.get_memory(project_id)

    def recall_key(self, project_id: str, key: str, default: str = "") -> str:
        return self.db.get_memory(project_id).get(key, default)

    def remember_entry(self, project_id: str, kind: str, key: str, value: str,
                       source: str = "", task_id: str = "") -> dict:
        """Record structured knowledge AND mirror it into legacy facts.

        The legacy key ``kind:key`` is only overwritten when the value changed,
        so existing planner keys (e.g. ``blueprint_analysis``) are untouched.
        """
        result = self.db.remember_entry(
            project_id, kind, key, value, source=source, task_id=task_id,
            max_versions=self.max_versions)
        if result.get("ok") and not result.get("deduped"):
            facts = self.db.get_memory(project_id)
            facts[f"{kind}:{key}"] = str(value)
            self.db.set_memory(project_id, facts)
        return result

    def search(self, project_id: str, kind: str = "", query: str = "",
               limit: int = 100, include_history: bool = False) -> list[dict]:
        return self.db.search_memory_entries(
            project_id, kind=kind, query=query, limit=limit, include_history=include_history)

    def history(self, project_id: str, kind: str, key: str) -> list[dict]:
        return self.db.memory_entry_history(project_id, kind, key)

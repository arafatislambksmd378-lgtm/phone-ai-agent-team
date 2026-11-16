"""Agent 7: Database Engineer — Designs schemas, migrations, queries, integrity."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "database_engineer"
    name = "Database Engineer"
    description = "Designs schemas, migrations, queries, integrity."
    system_prompt = "You are the Database Engineer. Designs schemas, migrations, queries, integrity. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        schema = """-- SQLite schema (WAL)\nCREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));\nCREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);\n"""
        self._write("database/schema.sql", schema)
        self._write("database/migrate.py", '"""Apply schema.sql to SQLite."""\nimport sqlite3, pathlib\nsql = pathlib.Path(__file__).with_name("schema.sql").read_text()\ncon = sqlite3.connect("app.db"); con.executescript(sql); con.commit(); con.close()\nprint("migrated")\n')
        return AgentResult(ok=True, summary="SQLite schema + migration generated.", files=["database/schema.sql", "database/migrate.py"])

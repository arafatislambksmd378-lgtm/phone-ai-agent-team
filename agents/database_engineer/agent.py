"""Agent 7 — Database Engineer: schema, migration, and a verified apply step."""
from __future__ import annotations
import sqlite3
import tempfile
from pathlib import Path

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a database engineer. Reply with a single SQLite SQL script:
tables, indexes, foreign keys, sensible constraints. Idempotent (IF NOT EXISTS)."""

SCHEMA = """-- SQLite schema (WAL). Idempotent.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    notes      TEXT    NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    done       INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_done    ON items(done);
"""

MIGRATE = '''"""Apply schema.sql to a SQLite database (idempotent)."""
import sqlite3
import sys
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else "app.db"
sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
con = sqlite3.connect(DB)
try:
    con.executescript(sql)
    con.commit()
    print(f"migrated {DB}")
finally:
    con.close()
'''


class Agent(BaseAgent):
    agent_id = "database_engineer"
    name = "Database Engineer"
    description = "Designs schemas, migrations and integrity constraints."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        gen = self.generate(
            f"Requirements:\n{self._read('docs/requirements.md')[:6000]}\n\nWrite schema.sql.",
            SCHEMA)
        sql = gen.text if "CREATE TABLE" in gen.text.upper() else SCHEMA
        self._write("database/schema.sql", sql)
        self._write("database/migrate.py", MIGRATE)
        ok, detail = self._verify(sql)
        return AgentResult(ok=ok, summary=f"Schema {'verified' if ok else 'INVALID'} — {detail}",
                           files=["database/schema.sql", "database/migrate.py"],
                           mode=gen.mode, error="" if ok else detail)

    def _verify(self, sql: str) -> tuple[bool, str]:
        """Apply the schema to a throwaway DB so we never ship broken SQL."""
        try:
            with tempfile.TemporaryDirectory() as d:
                con = sqlite3.connect(str(Path(d) / "verify.db"))
                try:
                    con.executescript(sql)
                    con.commit()
                    tables = [r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                finally:
                    con.close()
            return (bool(tables), f"{len(tables)} table(s): {', '.join(tables) or 'none'}")
        except sqlite3.Error as e:
            return False, f"SQL error: {e}"

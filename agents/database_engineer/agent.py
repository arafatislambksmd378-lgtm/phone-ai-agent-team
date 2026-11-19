"""Agent 7 — Database Engineer: entity-aware SQLite schema, migration runner, and live SQL verification."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from agents.base import AgentResult, BaseAgent
from agents.database_engineer.prompts import SYSTEM_PROMPT, build_user_prompt
from agents.helpers import extract_code_block, primary_entity_from, title_from

MIGRATE_SCRIPT = '''"""Apply schema.sql to a SQLite database (idempotent, WAL-enabled)."""
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


def build_default_schema(entity: str) -> str:
    base = """-- SQLite schema (WAL). Idempotent.
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
    if entity and entity != "items" and entity.isidentifier():
        base += f"""
CREATE TABLE IF NOT EXISTS {entity} (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
    notes      TEXT    NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    done       INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_{entity}_created ON {entity}(created_at DESC);
"""
    return base


class Agent(BaseAgent):
    agent_id = "database_engineer"
    name = "Database Engineer"
    description = "Designs idempotent SQLite schemas, migrations, indexes, and verifies SQL execution."
    responsibilities = [
        "Design normalized idempotent SQLite schema tailored to extracted domain entities",
        "Strip Markdown code fences and verify SQL against a live temporary SQLite database",
        "Generate repeatable database/migrate.py migration runner",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["database/schema.sql", "database/migrate.py"],
        "required_data_keys": ["tables", "verified"],
    }
    completion_criteria = ["database/schema.sql executes cleanly in SQLite and creates >= 1 table"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/architecture.md", "docs/requirements.json"])
        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        entity, _ = primary_entity_from(ctx["entities"], ctx["requirements"])
        fallback = build_default_schema(entity)

        prompt = build_user_prompt(
            title=title,
            entities=ctx["entities"],
            spec_md=ctx["requirements"],
            arch_md=ctx["files"].get("docs/architecture.md", ""),
        )

        def _validate_sql(raw: str) -> tuple[bool, str]:
            sql_cand = extract_code_block(raw, prefer_tag="sql")
            if "CREATE TABLE" not in sql_cand.upper():
                return False, "Missing CREATE TABLE statement"
            ok_v, info, _ = self._verify(sql_cand)
            if not ok_v:
                return False, info
            return True, sql_cand.rstrip() + "\n"

        gen = self.generate(prompt, fallback, validator=_validate_sql)
        sql = gen.text if "CREATE TABLE" in gen.text.upper() else fallback
        ok, detail, tables = self._verify(sql)
        if not ok:
            sql = fallback
            ok, detail, tables = self._verify(sql)

        self._write("database/schema.sql", sql)
        self._write("database/migrate.py", MIGRATE_SCRIPT)
        return AgentResult(
            ok=ok,
            summary=f"Schema {'verified' if ok else 'INVALID'} ({gen.mode}) — {detail}",
            files=["database/schema.sql", "database/migrate.py"],
            data={"tables": tables, "verified": ok, "entity": entity},
            mode=gen.mode,
            error="" if ok else detail,
        )

    @staticmethod
    def _verify(sql: str) -> tuple[bool, str, list[str]]:
        """Apply the schema to a throwaway DB so broken SQL is never handed off."""
        try:
            with tempfile.TemporaryDirectory() as d:
                con = sqlite3.connect(str(Path(d) / "verify.db"))
                try:
                    con.executescript(sql)
                    con.commit()
                    tables = [r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )]
                finally:
                    con.close()
            return (bool(tables), f"{len(tables)} table(s): {', '.join(tables) or 'none'}", tables)
        except sqlite3.Error as e:
            return False, f"SQL error: {e}", []

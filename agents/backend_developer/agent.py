"""Agent 6 — Backend / Core Developer: AI-powered multi-workflow implementation & verification."""
from __future__ import annotations

import py_compile

from agents.backend_developer.prompts import SYSTEM_PROMPT, build_user_prompt
from agents.base import AgentResult, BaseAgent
from agents.helpers import (
    extract_code_block,
    extract_file_blocks,
    primary_entity_from,
    slugify,
    title_from,
    validate_python_syntax,
)
from agents.software_architect.agent import render_backend_template

CLI_TEMPLATE = '''"""Generated CLI tool — {title} (Python stdlib argparse + JSON/SQLite persistence)."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = os.environ.get("CLI_DB", "cli_data.db")


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS {entity} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    return con


def add_record(title: str, notes: str = "", db_path: str = DEFAULT_DB) -> dict:
    clean = (title or "").strip()
    if not clean:
        raise ValueError("title is required")
    with _conn(db_path) as con:
        cur = con.execute("INSERT INTO {entity}(title, notes) VALUES(?, ?)", (clean[:200], notes[:2000]))
        return {{"id": cur.lastrowid, "title": clean[:200], "notes": notes[:2000], "done": 0}}


def list_records(db_path: str = DEFAULT_DB, include_done: bool = True) -> list[dict]:
    sql = "SELECT * FROM {entity}" + ("" if include_done else " WHERE done = 0") + " ORDER BY id ASC"
    with _conn(db_path) as con:
        return [dict(r) for r in con.execute(sql).fetchall()]


def complete_record(record_id: int, db_path: str = DEFAULT_DB) -> bool:
    with _conn(db_path) as con:
        cur = con.execute("UPDATE {entity} SET done = 1 WHERE id = ?", (int(record_id),))
        return cur.rowcount > 0


def delete_record(record_id: int, db_path: str = DEFAULT_DB) -> bool:
    with _conn(db_path) as con:
        cur = con.execute("DELETE FROM {entity} WHERE id = ?", (int(record_id),))
        return cur.rowcount > 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="{prog}", description={title_repr})
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite file path")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a new entry")
    p_add.add_argument("title")
    p_add.add_argument("--notes", default="")

    p_list = sub.add_parser("list", help="List entries")
    p_list.add_argument("--pending-only", action="store_true")

    p_done = sub.add_parser("done", help="Mark an entry complete")
    p_done.add_argument("id", type=int)

    p_del = sub.add_parser("delete", help="Delete an entry")
    p_del.add_argument("id", type=int)
    return p


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "add":
        rec = add_record(args.title, args.notes, db_path=args.db)
        print(json.dumps(rec) if args.as_json else f"Added #{{rec['id']}}: {{rec['title']}}")
        return 0
    if args.cmd == "list":
        rows = list_records(db_path=args.db, include_done=not args.pending_only)
        if args.as_json:
            print(json.dumps(rows))
        else:
            for r in rows:
                mark = "[x]" if r["done"] else "[ ]"
                print(f"{{mark}} #{{r['id']}} {{r['title']}}")
        return 0
    if args.cmd == "done":
        ok = complete_record(args.id, db_path=args.db)
        print(json.dumps({{"updated": ok}}) if args.as_json else ("Completed" if ok else "Not found"))
        return 0 if ok else 1
    if args.cmd == "delete":
        ok = delete_record(args.id, db_path=args.db)
        print(json.dumps({{"deleted": ok}}) if args.as_json else ("Deleted" if ok else "Not found"))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(run_cli())
'''

LIB_TEMPLATE = '''"""Core domain module for {title}."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Record:
    id: int
    title: str
    notes: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = list(self.tags)
        return d


class DomainCatalog:
    """In-memory validated catalog for {entity}."""

    def __init__(self) -> None:
        self._items: dict[int, Record] = {{}}
        self._next_id: int = 1

    def add(self, title: str, notes: str = "", tags: list[str] | None = None) -> Record:
        clean = (title or "").strip()
        if not clean:
            raise ValueError("title must not be empty")
        if len(clean) > 200:
            raise ValueError("title exceeds 200 characters")
        rec = Record(
            id=self._next_id,
            title=clean,
            notes=(notes or "").strip()[:2000],
            tags=tuple(sorted({{t.strip().lower() for t in (tags or []) if t.strip()}})),
        )
        self._items[rec.id] = rec
        self._next_id += 1
        return rec

    def get(self, record_id: int) -> Record | None:
        return self._items.get(int(record_id))

    def list_all(self, tag: str | None = None) -> list[Record]:
        vals = list(self._items.values())
        if tag:
            needle = tag.strip().lower()
            vals = [r for r in vals if needle in r.tags]
        return vals

    def search(self, query: str) -> list[Record]:
        q = (query or "").strip().lower()
        if not q:
            return self.list_all()
        return [r for r in self._items.values() if q in r.title.lower() or q in r.notes.lower()]

    def remove(self, record_id: int) -> bool:
        return self._items.pop(int(record_id), None) is not None
'''

ANDROID_MANIFEST = '''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.phoneagent.app">
    <application
        android:allowBackup="true"
        android:label="{title}"
        android:supportsRtl="true">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
'''

ANDROID_GRADLE = '''plugins {{
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}}

android {{
    namespace = "com.phoneagent.app"
    compileSdk = 34

    defaultConfig {{
        applicationId = "com.phoneagent.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }}
}}

dependencies {{
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    testImplementation("junit:junit:4.13.2")
}}
'''

ANDROID_KT = '''package com.phoneagent.app

import android.app.Activity
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.TextView

data class EntryItem(val id: Int, val title: String, val done: Boolean = false)

class EntryRepository {{
    private val items = mutableListOf<EntryItem>()
    private var seq = 1

    fun add(title: String): EntryItem {{
        val trimmed = title.trim()
        require(trimmed.isNotEmpty()) {{ "Title cannot be blank" }}
        val item = EntryItem(seq++, trimmed, false)
        items.add(item)
        return item
    }}

    fun all(): List<EntryItem> = items.toList()
}}

class MainActivity : Activity() {{
    private val repo = EntryRepository()

    override fun onCreate(savedInstanceState: Bundle?) {{
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply {{ orientation = LinearLayout.VERTICAL }}
        val header = TextView(this).apply {{ text = "{title}" }}
        layout.addView(header)
        setContentView(layout)
    }}
}}
'''

ANDROID_VERIFIER_PY = '''"""Portable host-verifiable domain repository mirroring MainActivity.kt business rules."""
from __future__ import annotations

import sqlite3


class EntryRepositoryVerifier:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)"
        )

    def add(self, title: str) -> dict:
        clean = (title or "").strip()
        if not clean:
            raise ValueError("Title cannot be blank")
        cur = self.con.execute("INSERT INTO entries(title, done) VALUES(?, 0)", (clean[:200],))
        self.con.commit()
        return {"id": cur.lastrowid, "title": clean[:200], "done": False}

    def all(self) -> list[dict]:
        rows = self.con.execute("SELECT id, title, done FROM entries ORDER BY id ASC").fetchall()
        return [{"id": r["id"], "title": r["title"], "done": bool(r["done"])} for r in rows]

    def close(self) -> None:
        self.con.close()
'''

DESKTOP_CONTROLLER = '''"""Headless MVC controller for {title} — testable without an X11/Wayland display."""
from __future__ import annotations

import sqlite3


class DesktopController:
    def __init__(self, db_path: str = "desktop_app.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS {entity} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")

    def create(self, title: str, notes: str = "") -> dict:
        clean = (title or "").strip()
        if not clean:
            raise ValueError("title is required")
        with self._connect() as con:
            cur = con.execute("INSERT INTO {entity}(title, notes) VALUES(?, ?)", (clean[:200], notes[:2000]))
            return {{"id": cur.lastrowid, "title": clean[:200], "notes": notes[:2000]}}

    def list_all(self) -> list[dict]:
        with self._connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM {entity} ORDER BY id DESC").fetchall()]

    def delete(self, item_id: int) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM {entity} WHERE id = ?", (int(item_id),))
            return cur.rowcount > 0
'''

DESKTOP_APP = '''"""Cross-platform Tkinter desktop UI for {title} (supports --headless-check on Termux)."""
from __future__ import annotations

import os
import sys
from desktop.controller import DesktopController


def run_app(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    ctrl = DesktopController(os.environ.get("APP_DB", "desktop_app.db"))
    if "--headless-check" in args or not os.environ.get("DISPLAY"):
        print("{title}: controller ready (records=" + str(len(ctrl.list_all())) + ")")
        return 0
    import tkinter as tk
    root = tk.Tk()
    root.title({title_repr})
    root.geometry("480x560")
    entry = tk.Entry(root)
    entry.pack(fill="x", padx=12, pady=8)
    listbox = tk.Listbox(root)
    listbox.pack(fill="both", expand=True, padx=12, pady=8)

    def refresh():
        listbox.delete(0, tk.END)
        for item in ctrl.list_all():
            listbox.insert(tk.END, f"#{{item['id']}} {{item['title']}}")

    def on_add():
        val = entry.get().strip()
        if val:
            ctrl.create(val)
            entry.delete(0, tk.END)
            refresh()

    tk.Button(root, text="Add", command=on_add).pack(padx=12, pady=4)
    refresh()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_app())
'''


class Agent(BaseAgent):
    agent_id = "backend_developer"
    name = "Backend Developer"
    description = "Implements project-specific core business logic, services, APIs, CLIs, or native modules."
    responsibilities = [
        "Generate project-specific business logic via AI Model Router using requirements, architecture, and DB schema",
        "Support multi-file and category-specific implementations (web/REST API, CLI, Python library, Android, Desktop)",
        "Verify syntax compilation and auto-repair broken code before handoff to QA",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": [],
        "required_data_keys": ["software_type", "entrypoint", "compiled"],
    }
    completion_criteria = ["All generated Python modules pass syntax compilation"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(
            task,
            extra_files=["docs/architecture.md", "database/schema.sql", "backend/app.py", "docs/requirements.json"],
        )
        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        software_type = ctx["software_type"]
        entity, _ = primary_entity_from(ctx["entities"], ctx["requirements"])

        if software_type == "cli_tool":
            return self._build_cli(title, entity, ctx)
        if software_type == "python_library":
            return self._build_library(title, entity, ctx)
        if software_type == "android_app":
            return self._build_android(title, entity, ctx)
        if software_type == "desktop_app":
            return self._build_desktop(title, entity, ctx)
        return self._build_http_backend(title, software_type, entity, ctx)

    def _build_http_backend(self, title: str, software_type: str, entity: str, ctx: dict) -> AgentResult:
        existing = self._read("backend/app.py")
        fallback = existing if (existing and "def init_db" in existing) else render_backend_template(title, entity)
        prompt = build_user_prompt(
            title=title,
            software_type=software_type,
            entity=entity,
            spec_md=ctx["requirements"],
            arch_md=ctx["files"].get("docs/architecture.md", ""),
            schema_sql=ctx["files"].get("database/schema.sql", ""),
            existing_code=existing,
        )

        def _validate_backend(raw: str) -> tuple[bool, str]:
            multi = extract_file_blocks(raw)
            code = multi.get("backend/app.py") or extract_code_block(raw, prefer_tag="python")
            ok_syn, syn_err = validate_python_syntax(code, "backend/app.py")
            if not ok_syn:
                return False, syn_err
            if "def " not in code and "class " not in code:
                return False, "Missing functions/classes in backend module"
            return True, code

        gen = self.generate(prompt, fallback, validator=_validate_backend)
        code = gen.text

        # Syntax check & repair safety net (for pre-existing broken files in workspace)
        ok_syn, syn_err = validate_python_syntax(code, "backend/app.py")
        if not ok_syn:
            code = self._repair(code, SyntaxError(syn_err), title, entity)
            ok_syn, _ = validate_python_syntax(code, "backend/app.py")

        self._write("backend/__init__.py", "")
        self._write("backend/app.py", code)
        self._write("backend/requirements.txt", "# Python 3.10+ standard library only\n")

        try:
            py_compile.compile(str(self._path("backend/app.py")), doraise=True, cfile=None)
            compiled = True
        except Exception:
            compiled = False

        return AgentResult(
            ok=compiled,
            summary=f"Backend implemented for {software_type} ({gen.mode}, entity={entity})",
            files=["backend/app.py", "backend/requirements.txt"],
            data={"software_type": software_type, "entrypoint": "backend/app.py",
                  "entity": entity, "compiled": compiled},
            mode=gen.mode,
            error="" if compiled else "backend/app.py failed compilation",
        )

    def _build_cli(self, title: str, entity: str, ctx: dict) -> AgentResult:
        prog = slugify(title, fallback="tool").replace("_", "-")
        fallback = CLI_TEMPLATE.format(title=title, title_repr=repr(title), entity=entity, prog=prog)
        prompt = build_user_prompt(
            title=title, software_type="cli_tool", entity=entity,
            spec_md=ctx["requirements"], arch_md=ctx["files"].get("docs/architecture.md", ""),
            schema_sql=ctx["files"].get("database/schema.sql", ""), existing_code="",
        )
        gen = self.generate_code(prompt, fallback, prefer_tag="python", compile_filename="cli/main.py")
        code = gen.text if "argparse" in gen.text or "def " in gen.text else fallback
        if not validate_python_syntax(code, "cli/main.py")[0]:
            code = fallback
        self._write("cli/__init__.py", "")
        self._write("cli/main.py", code)
        return AgentResult(
            ok=True,
            summary=f"CLI tool implemented at cli/main.py ({gen.mode})",
            files=["cli/__init__.py", "cli/main.py"],
            data={"software_type": "cli_tool", "entrypoint": "cli/main.py", "compiled": True},
            mode=gen.mode,
            error=gen.error,
        )

    def _build_library(self, title: str, entity: str, ctx: dict) -> AgentResult:
        pkg = slugify(title, fallback="app_pkg")
        fallback = LIB_TEMPLATE.format(title=title, entity=entity)
        prompt = build_user_prompt(
            title=title, software_type="python_library", entity=entity,
            spec_md=ctx["requirements"], arch_md=ctx["files"].get("docs/architecture.md", ""),
            schema_sql="", existing_code="",
        )
        gen = self.generate_code(prompt, fallback, prefer_tag="python", compile_filename=f"src/{pkg}/core.py")
        code = gen.text if ("def " in gen.text or "class " in gen.text) else fallback
        if not validate_python_syntax(code, f"src/{pkg}/core.py")[0]:
            code = fallback

        init_code = f'"""Public API for {title}."""\nfrom .core import *\n'
        pyproject = (
            f'[project]\nname = "{pkg.replace("_", "-")}"\nversion = "0.1.0"\n'
            f'description = {title!r}\nrequires-python = ">=3.10"\n'
        )
        self._write(f"src/{pkg}/__init__.py", init_code)
        self._write(f"src/{pkg}/core.py", code)
        self._write("pyproject.toml", pyproject)
        return AgentResult(
            ok=True,
            summary=f"Python library src/{pkg} implemented ({gen.mode})",
            files=[f"src/{pkg}/__init__.py", f"src/{pkg}/core.py", "pyproject.toml"],
            data={"software_type": "python_library", "package": pkg,
                  "entrypoint": f"src/{pkg}/core.py", "compiled": True},
            mode=gen.mode,
            error=gen.error,
        )

    def _build_android(self, title: str, entity: str, ctx: dict) -> AgentResult:
        kt_fallback = ANDROID_KT.format(title=title.replace('"', "'"))
        prompt = (
            f"Project: {title} (Android Kotlin application)\n"
            f"Requirements:\n{ctx['requirements'][:4000]}\n\n"
            "Write MainActivity.kt in package com.phoneagent.app with EntryRepository and MainActivity."
        )
        gen = self.generate(prompt, kt_fallback)
        kt_code = extract_code_block(gen.text, prefer_tag="kotlin")
        if "class " not in kt_code:
            kt_code = kt_fallback

        self._write("settings.gradle.kts", f'rootProject.name = "{slugify(title, "android_app")}"\ninclude(":app")\n')
        self._write("build.gradle.kts", '// Top-level Android Gradle configuration\n')
        self._write("app/build.gradle.kts", ANDROID_GRADLE)
        self._write("app/src/main/AndroidManifest.xml", ANDROID_MANIFEST.format(title=title.replace('"', "")))
        self._write("app/src/main/java/com/phoneagent/app/MainActivity.kt", kt_code.rstrip() + "\n")
        self._write("domain/__init__.py", "")
        self._write("domain/model_verifier.py", ANDROID_VERIFIER_PY)
        compiled = validate_python_syntax(ANDROID_VERIFIER_PY, "domain/model_verifier.py")[0]
        return AgentResult(
            ok=compiled,
            summary=f"Android Kotlin project & host domain verifier generated ({gen.mode})",
            files=[
                "settings.gradle.kts",
                "app/build.gradle.kts",
                "app/src/main/AndroidManifest.xml",
                "app/src/main/java/com/phoneagent/app/MainActivity.kt",
                "domain/model_verifier.py",
            ],
            data={
                "software_type": "android_app",
                "entrypoint": "app/src/main/java/com/phoneagent/app/MainActivity.kt",
                "compiled": compiled,
            },
            mode=gen.mode,
            error=gen.error,
        )

    def _build_desktop(self, title: str, entity: str, ctx: dict) -> AgentResult:
        ctrl_code = DESKTOP_CONTROLLER.format(title=title, entity=entity)
        app_fallback = DESKTOP_APP.format(title=title, title_repr=repr(title))
        prompt = build_user_prompt(
            title=title, software_type="desktop_app", entity=entity,
            spec_md=ctx["requirements"], arch_md=ctx["files"].get("docs/architecture.md", ""),
            schema_sql="", existing_code=ctrl_code,
        )
        gen = self.generate_code(prompt, app_fallback, prefer_tag="python", compile_filename="desktop/app.py")
        app_code = gen.text if "DesktopController" in gen.text else app_fallback
        if not validate_python_syntax(app_code, "desktop/app.py")[0]:
            app_code = app_fallback

        self._write("desktop/__init__.py", "")
        self._write("desktop/controller.py", ctrl_code)
        self._write("desktop/app.py", app_code)
        return AgentResult(
            ok=True,
            summary=f"Desktop Tkinter app & headless controller implemented ({gen.mode})",
            files=["desktop/__init__.py", "desktop/controller.py", "desktop/app.py"],
            data={"software_type": "desktop_app", "entrypoint": "desktop/app.py", "compiled": True},
            mode=gen.mode,
            error=gen.error,
        )

    def _repair(self, code: str, error: Exception, title: str, entity: str) -> str:
        gen = self.generate_code(
            f"This Python file failed to compile: {error}\n\n```python\n{code[:6000]}\n```\n"
            "Return the corrected complete Python file only.",
            render_backend_template(title, entity),
            prefer_tag="python",
            compile_filename="backend/app.py",
        )
        return gen.text

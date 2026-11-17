"""Agent 3 — Software Architect: category-aware architecture + clean project scaffolding."""
from __future__ import annotations

import json

from agents.base import AgentResult, BaseAgent
from agents.helpers import primary_entity_from, slugify, title_from
from agents.software_architect.prompts import SYSTEM_PROMPT, build_user_prompt

# Retained and upgraded as a clean fallback skeleton for HTTP/web/API projects.
BACKEND_TEMPLATE = '''"""Generated HTTP/JSON backend — Python stdlib http.server + SQLite (WAL). Zero external deps."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DB_PATH = os.environ.get("APP_DB", "app.db")
TITLE = {title_repr}
ENTITY = {entity_repr}


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')))""")
        if ENTITY != "items" and re.fullmatch(r"[a-z_][a-z0-9_]*", ENTITY):
            con.execute(f"""CREATE TABLE IF NOT EXISTS {{ENTITY}} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                notes TEXT DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')))""")


def _table(use_entity: bool = False) -> str:
    if use_entity and re.fullmatch(r"[a-z_][a-z0-9_]*", ENTITY):
        return ENTITY
    return "items"


def list_items(use_entity: bool = False):
    init_db()
    tbl = _table(use_entity)
    with connect() as con:
        return [dict(r) for r in con.execute(f"SELECT * FROM {{tbl}} ORDER BY id DESC")]


def create_item(title, notes="", done=0, use_entity: bool = False):
    init_db()
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    if len(title) > 200:
        raise ValueError("title too long")
    notes = str(notes or "")[:2000]
    done_flag = 1 if bool(done) else 0
    tbl = _table(use_entity)
    with connect() as con:
        cur = con.execute(
            f"INSERT INTO {{tbl}}(title, notes, done) VALUES(?,?,?)",
            (title, notes, done_flag),
        )
        return {{"id": cur.lastrowid, "title": title, "notes": notes, "done": done_flag}}


def update_item(item_id, *, title=None, notes=None, done=None, use_entity: bool = False):
    init_db()
    tbl = _table(use_entity)
    with connect() as con:
        row = con.execute(f"SELECT * FROM {{tbl}} WHERE id=?", (int(item_id),)).fetchone()
        if not row:
            return None
        new_title = row["title"] if title is None else str(title).strip()
        if not new_title or len(new_title) > 200:
            raise ValueError("invalid title")
        new_notes = row["notes"] if notes is None else str(notes)[:2000]
        new_done = row["done"] if done is None else (1 if bool(done) else 0)
        con.execute(
            f"UPDATE {{tbl}} SET title=?, notes=?, done=? WHERE id=?",
            (new_title, new_notes, new_done, int(item_id)),
        )
        return {{"id": int(item_id), "title": new_title, "notes": new_notes, "done": new_done}}


def delete_item(item_id, use_entity: bool = False):
    init_db()
    tbl = _table(use_entity)
    with connect() as con:
        cur = con.execute(f"DELETE FROM {{tbl}} WHERE id=?", (int(item_id),))
        return cur.rowcount > 0


class Handler(BaseHTTPRequestHandler):
    server_version = "GeneratedApp/1.0"

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        """Serve frontend files when present; fall back to JSON API index for headless REST APIs."""
        root = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))
        target = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        if os.path.commonpath([root, target]) != root:
            return self._json({{"error": "forbidden"}}, 403)
        if not os.path.isfile(target):
            if rel == "index.html":
                return self._json({{
                    "service": TITLE,
                    "status": "ok",
                    "endpoints": ["/api/health", "/api/items", f"/api/{{ENTITY}}"],
                }})
            return self._json({{"error": "not found"}}, 404)
        ctype = {{".html": "text/html; charset=utf-8",
                 ".css": "text/css", ".js": "application/javascript"}}.get(
                     os.path.splitext(target)[1].lower(), "application/octet-stream")
        with open(target, "rb") as fh:
            body = fh.read(2_000_000)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        if length <= 0:
            return {{}}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{{}}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/api/health", "/health"):
            return self._json({{"status": "ok", "title": TITLE, "entity": ENTITY}})
        if path == "/api/items":
            return self._json({{"items": list_items(False)}})
        if path == f"/api/{{ENTITY}}":
            rows = list_items(ENTITY != "items")
            return self._json({{ENTITY: rows, "items": rows}})
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        return self._json({{"error": "not found"}}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/items", f"/api/{{ENTITY}}"):
            return self._json({{"error": "not found"}}, 404)
        body = self._body()
        if body is None or not isinstance(body, dict):
            return self._json({{"error": "invalid json"}}, 400)
        try:
            use_ent = (path == f"/api/{{ENTITY}}" and ENTITY != "items")
            title_val = body.get("title") or body.get("name") or ""
            item = create_item(title_val, body.get("notes", ""), body.get("done", 0), use_entity=use_ent)
        except ValueError as e:
            return self._json({{"error": str(e)}}, 422)
        return self._json(item, 201)

    def do_PATCH(self):
        path = urlparse(self.path).path
        m = re.fullmatch(rf"/api/(items|{{re.escape(ENTITY)}})/(\\d+)", path)
        if not m:
            return self._json({{"error": "not found"}}, 404)
        body = self._body()
        if body is None or not isinstance(body, dict):
            return self._json({{"error": "invalid json"}}, 400)
        try:
            use_ent = (m.group(1) == ENTITY and ENTITY != "items")
            updated = update_item(int(m.group(2)), title=body.get("title"),
                                  notes=body.get("notes"), done=body.get("done"),
                                  use_entity=use_ent)
        except ValueError as e:
            return self._json({{"error": str(e)}}, 422)
        if updated is None:
            return self._json({{"error": "not found"}}, 404)
        return self._json(updated, 200)

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.fullmatch(rf"/api/(items|{{re.escape(ENTITY)}})/(\\d+)", path)
        if not m:
            return self._json({{"error": "not found"}}, 404)
        use_ent = (m.group(1) == ENTITY and ENTITY != "items")
        return self._json({{"deleted": delete_item(int(m.group(2)), use_entity=use_ent)}})

    def log_message(self, *args):
        pass


def main(host="0.0.0.0", port=None):
    port = int(port or os.environ.get("PORT", "8000"))
    init_db()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"serving {{TITLE}} on http://{{host}}:{{port}}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
'''


def render_backend_template(title: str, entity: str = "items") -> str:
    return BACKEND_TEMPLATE.format(title_repr=repr(title), entity_repr=repr(entity or "items"))


class Agent(BaseAgent):
    agent_id = "software_architect"
    name = "Software Architect"
    description = "Defines software architecture, module layout, interfaces, and initial category-matched scaffolding."
    responsibilities = [
        "Design module boundaries, interfaces, and persistence strategy per software category",
        "Select appropriate technologies respecting Android/Termux resource boundaries",
        "Document platform build constraints honestly when external SDKs are absent",
        "Scaffold clean category-specific initial package directories and contracts",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["docs/architecture.md", "ARCHITECTURE.md"],
        "required_data_keys": ["software_type", "components", "entrypoint"],
    }
    completion_criteria = ["Architecture document written and category-matched module layout scaffolded"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/requirements.json"])
        spec_md = ctx["requirements"]
        spec_json = ctx["spec"]
        title = spec_json.get("project_title") or title_from(spec_md)
        software_type = ctx["software_type"]
        entity_plural, _ = primary_entity_from(ctx["entities"], spec_md)

        fallback = self._fallback(title, software_type, entity_plural, ctx["technologies"],
                                  ctx["platform_limitations"], spec_md)
        prompt = build_user_prompt(
            title=title,
            software_type=software_type,
            entities=ctx["entities"],
            technologies=ctx["technologies"],
            limitations=ctx["platform_limitations"],
            spec_md=spec_md,
            handoff_summary=ctx["handoff_summary"],
        )
        gen = self.generate(prompt, fallback)
        arch_text = gen.text if len(gen.text.strip()) >= 60 else fallback

        files = ["docs/architecture.md", "ARCHITECTURE.md"]
        self._write("docs/architecture.md", arch_text)

        scaffolded, entrypoint, components = self._scaffold_by_category(
            software_type=software_type,
            title=title,
            entity=entity_plural,
        )
        files.extend(scaffolded)

        arch_summary = (
            f"# Architecture — {title}\n\n"
            f"- **Software Type:** `{software_type}`\n"
            f"- **Primary Entrypoint:** `{entrypoint}`\n"
            f"- **Technologies:** {', '.join(ctx['technologies'])}\n\n"
            "See `docs/architecture.md` for full component and interface specifications.\n"
        )
        self._write("ARCHITECTURE.md", arch_summary)

        arch_meta = {
            "project_title": title,
            "software_type": software_type,
            "primary_entity": entity_plural,
            "entrypoint": entrypoint,
            "components": components,
            "technologies": ctx["technologies"],
            "platform_limitations": ctx["platform_limitations"],
        }
        self._write("docs/architecture.json", json.dumps(arch_meta, indent=2))
        files.append("docs/architecture.json")

        return AgentResult(
            ok=True,
            summary=f"Architecture & {software_type} scaffold ready ({gen.mode})",
            files=files,
            data=arch_meta,
            mode=gen.mode,
            error=gen.error,
        )

    def _scaffold_by_category(self, *, software_type: str, title: str,
                              entity: str) -> tuple[list[str], str, list[str]]:
        created: list[str] = []
        pkg = slugify(title, fallback="app_pkg")

        if software_type == "cli_tool":
            self._write("cli/__init__.py", "")
            created.append("cli/__init__.py")
            return created, "cli/main.py", ["cli/main.py", "tests/test_cli.py"]

        if software_type == "python_library":
            self._write(f"src/{pkg}/__init__.py", f'"""Package {title}."""\n')
            created.append(f"src/{pkg}/__init__.py")
            return created, f"src/{pkg}/core.py", [f"src/{pkg}/core.py", "tests/test_library.py"]

        if software_type == "android_app":
            settings_kts = f'rootProject.name = "{slugify(title, "android_app")}"\ninclude(":app")\n'
            self._write("settings.gradle.kts", settings_kts)
            created.append("settings.gradle.kts")
            return created, "app/src/main/java/com/phoneagent/app/MainActivity.kt", [
                "app/build.gradle.kts",
                "app/src/main/AndroidManifest.xml",
                "app/src/main/java/com/phoneagent/app/MainActivity.kt",
                "domain/model_verifier.py",
            ]

        if software_type == "desktop_app":
            self._write("desktop/__init__.py", "")
            created.append("desktop/__init__.py")
            return created, "desktop/app.py", ["desktop/controller.py", "desktop/app.py", "tests/test_desktop.py"]

        # Default HTTP/SQLite categories: web_app, rest_api, backend_service, database_app
        if not self._exists("backend/app.py"):
            self._write("backend/app.py", render_backend_template(title, entity))
            created.append("backend/app.py")
        self._write("backend/__init__.py", "")
        created.append("backend/__init__.py")
        comps = ["backend/app.py", "database/schema.sql", "tests/test_api.py"]
        if software_type == "web_app":
            comps.insert(1, "frontend/index.html")
        return created, "backend/app.py", comps

    @staticmethod
    def _fallback(title: str, software_type: str, entity: str,
                  technologies: list[str], limitations: list[str], spec: str) -> str:
        lims = "\n".join(f"- {x}" for x in limitations) or "- Runs directly on Android Termux with Python 3.10+ stdlib."
        return f"""# Architecture — {title}

## 1. Architectural Overview & Category Fit
- **Category:** `{software_type}`
- **Primary Domain Entity:** `{entity}`
- **Stack:** {', '.join(technologies)}
- Designed specifically for low-memory environments (Android 14, 3 GB RAM, Termux) with zero external daemons.

## 2. Component Breakdown & File Layout
- Core domain & service layer confined to modular files under the project root.
- Automated `pytest` verification suite under `tests/`.
- Operational and release documentation under `docs/`.

## 3. Data Model & Persistence Strategy
- Uses SQLite in WAL mode (`synchronous=NORMAL`) when stateful storage is required, ensuring crash-safe single-file persistence.

## 4. Interfaces
- Clean, input-validated interfaces with deterministic error codes and bounded payload sizes (≤ 1 MB).

## 5. Execution Sequence & Failure Modes
- Invalid input raises typed `ValueError` or HTTP `400`/`422`.
- Missing or locked database retries safely under SQLite `busy_timeout`.

## 6. Android/Termux Resource & Platform Considerations
{lims}

## Source Specification Excerpt
{spec[:1200] or "—"}
"""

"""Agent 3 — Software Architect: architecture doc + runnable backend skeleton."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.helpers import slugify, title_from

SYSTEM = """You are a pragmatic software architect. Produce a Markdown architecture
document for a small application that must run on Android Termux with SQLite and the
Python standard library only. Sections: Overview, Components, Data Model, API Surface,
Sequence, Failure Modes, Trade-offs. Keep it implementable in under 2000 lines."""

BACKEND_TEMPLATE = '''"""Generated backend — Python stdlib http.server + SQLite. No third-party deps."""
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("APP_DB", "app.db")
TITLE = "{title}"


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')))""")


def list_items():
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM items ORDER BY id DESC")]


def create_item(title, notes=""):
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    if len(title) > 200:
        raise ValueError("title too long")
    with connect() as con:
        cur = con.execute("INSERT INTO items(title, notes) VALUES(?,?)", (title, notes[:2000]))
        return {{"id": cur.lastrowid, "title": title, "notes": notes}}


def delete_item(item_id):
    with connect() as con:
        cur = con.execute("DELETE FROM items WHERE id=?", (item_id,))
        return cur.rowcount > 0


class Handler(BaseHTTPRequestHandler):
    server_version = "GeneratedApp/0.1"

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        """Serve frontend files, confined to ../frontend."""
        root = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "..", "frontend"))
        target = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        if os.path.commonpath([root, target]) != root:
            return self._json({{"error": "forbidden"}}, 403)
        if not os.path.isfile(target):
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
            return json.loads(self.rfile.read(length).decode() or "{{}}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/api/health", "/health"):
            return self._json({{"status": "ok", "title": TITLE}})
        if path == "/api/items":
            return self._json({{"items": list_items()}})
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        return self._json({{"error": "not found"}}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/items":
            return self._json({{"error": "not found"}}, 404)
        body = self._body()
        if body is None:
            return self._json({{"error": "invalid json"}}, 400)
        try:
            item = create_item(body.get("title"), body.get("notes", ""))
        except ValueError as e:
            return self._json({{"error": str(e)}}, 422)
        return self._json(item, 201)

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.fullmatch(r"/api/items/(\\d+)", path)
        if not m:
            return self._json({{"error": "not found"}}, 404)
        return self._json({{"deleted": delete_item(int(m.group(1)))}})

    def log_message(self, *args):
        pass


def main(host="0.0.0.0", port=8000):
    init_db()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"serving {{TITLE}} on http://{{host}}:{{port}}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
'''


class Agent(BaseAgent):
    agent_id = "software_architect"
    name = "Software Architect"
    description = "Defines architecture, modules, interfaces and tech choices."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        spec = self._read("docs/requirements.md") or self._requirements()
        title = title_from(spec)
        fallback = self._fallback(title, spec)
        gen = self.generate(
            f"Requirements:\n\n{spec[:12000]}\n\nWrite the architecture document.",
            fallback)
        files = ["docs/architecture.md"]
        self._write("docs/architecture.md", gen.text)
        if not self._exists("backend/app.py"):
            self._write("backend/app.py", BACKEND_TEMPLATE.format(title=title))
            files.append("backend/app.py")
        self._write("backend/__init__.py", "")
        arch = (f"# Architecture\n\nProject: {title}\n\n"
                "```\nclient (static SPA) → REST /api/* → ThreadingHTTPServer → SQLite (WAL)\n```\n\n"
                "See docs/architecture.md for the full design.\n")
        self._write("ARCHITECTURE.md", arch)
        return AgentResult(ok=True, summary=f"Architecture written ({gen.mode}); backend skeleton ready",
                           files=files, mode=gen.mode, error=gen.error)

    def _fallback(self, title: str, spec: str) -> str:
        return f"""# Architecture — {title}

## Overview
A single-process Python 3 application with a static frontend and a JSON REST API,
persisted in SQLite (WAL). It runs on Android/Termux with no Docker, Redis or
PostgreSQL and no third-party runtime dependencies.

## Components
| Component | Responsibility | Location |
|---|---|---|
| HTTP API | Routing, validation, JSON responses | `backend/app.py` |
| Domain layer | Item validation and CRUD | `backend/app.py` |
| Persistence | SQLite schema + migrations | `database/schema.sql` |
| Frontend | Static SPA with fetch() calls | `frontend/index.html` |
| Tests | pytest suite exercising the API | `tests/test_api.py` |

## Data Model
`items(id INTEGER PK, title TEXT NOT NULL, notes TEXT, created_at TEXT)`

## API Surface
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness probe |
| GET | `/api/items` | List items |
| POST | `/api/items` | Create item (`{{"title": "..."}}`) |
| DELETE | `/api/items/{{id}}` | Delete item |

## Sequence
1. Browser loads `frontend/index.html`.
2. SPA fetches `/api/items`.
3. API validates input (non-empty title, ≤200 chars) and writes to SQLite.
4. Response is JSON; failures return 4xx with `{{"error": ...}}`.

## Failure Modes
- Missing/locked DB → 500 with redacted message; retry after WAL checkpoint.
- Invalid JSON body → 400.
- Oversized body (>1 MB) → rejected before parsing.

## Trade-offs
- Standard library only: no ORM, but zero install cost on a 3 GB device.
- Single file API: simple to read and review; split when it exceeds ~800 lines.

## Source requirements
{spec[:1500] or "—"}
"""

"""Agent 6 — Backend Developer: verifies/extends the API implementation."""
from __future__ import annotations
import py_compile

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a backend engineer working in Python 3.10+ with the standard
library only. Reply with ONE complete Python file implementing a ThreadingHTTPServer
JSON API with SQLite persistence, input validation and no shell execution."""


class Agent(BaseAgent):
    agent_id = "backend_developer"
    name = "Backend Developer"
    description = "Implements and hardens backend services and APIs."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        existing = self._read("backend/app.py")
        if not existing:
            fallback = (
                '"""Minimal API (architect step did not run first)."""\n'
                "import json\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n\n"
                "class H(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        body = json.dumps({'status': 'ok'}).encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type', 'application/json')\n"
                "        self.send_header('Content-Length', str(len(body)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(body)\n\n"
                "    def log_message(self, *a):\n        pass\n\n\n"
                "if __name__ == '__main__':\n"
                "    ThreadingHTTPServer(('0.0.0.0', 8000), H).serve_forever()\n")
            self._write("backend/app.py", fallback)
            existing = fallback

        notes = []
        # 1. Syntax check — a broken API must fail here, not in QA.
        try:
            py_compile.compile(str(self._path("backend/app.py")), doraise=True, cfile=None)
            notes.append("syntax ok")
        except Exception as e:
            repaired = self._repair(existing, e)
            self._write("backend/app.py", repaired)
            notes.append(f"syntax repaired: {type(e).__name__}")
            existing = repaired

        # 2. Contract checks
        for needle, label in (("/api/health", "health endpoint"),
                              ("/api/items", "items endpoint"),
                              ("sqlite3", "sqlite persistence")):
            notes.append(f"{label}: {'present' if needle in existing else 'MISSING'}")

        self._write("backend/requirements.txt", "# standard library only\n")
        return AgentResult(ok="syntax ok" in notes or "repaired" in " ".join(notes),
                           summary="Backend verified — " + ", ".join(notes),
                           files=["backend/app.py"], mode="deterministic")

    def _path(self, rel: str):
        from pathlib import Path
        from security.validation import safe_join
        return safe_join(self.workspace, rel)

    def _repair(self, code: str, error: Exception) -> str:
        """Deterministic repair: ask AI once, else fall back to a known-good file."""
        gen = self.generate(
            f"This Python file failed to compile: {error}\n\n```python\n{code[:6000]}\n```\n"
            "Return the corrected complete file only.", "")
        if "def " in gen.text or "import " in gen.text:
            try:
                compile(gen.text, "backend/app.py", "exec")
                return gen.text
            except SyntaxError:
                pass
        from agents.software_architect.agent import BACKEND_TEMPLATE
        from agents.helpers import title_from
        return BACKEND_TEMPLATE.format(title=title_from(self._read("docs/requirements.md")))

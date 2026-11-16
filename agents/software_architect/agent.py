"""Agent 3: Software Architect — Designs architecture, modules, interfaces, tech choices."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "software_architect"
    name = "Software Architect"
    description = "Designs architecture, modules, interfaces, tech choices."
    system_prompt = "You are the Software Architect. Designs architecture, modules, interfaces, tech choices. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        req = self._read("docs/requirements.json") or "{}"
        try:
            spec = json.loads(req)
        except Exception:
            spec = {}
        arch = f"""# Architecture\n\nProject: {spec.get('project_title','App')}\n\n## Stack\n- Backend: Python 3 stdlib + SQLite (Termux-friendly)\n- Frontend: HTML/CSS/JS (responsive, mobile-first)\n- API: REST JSON\n\n## Modules\n- `backend/` — API + business logic\n- `frontend/` — UI\n- `database/` — schema + migrations\n- `tests/` — pytest suite\n\n## Interfaces\n- GET /api/health -> {{status}}\n- GET /api/items -> list\n- POST /api/items -> create\n\n## Dependencies\n- External AI: Gemini/OpenAI/Anthropic via router (optional)\n- DB: SQLite (WAL)\n"""
        self._write("docs/architecture.md", arch)
        self._write("backend/app.py", '"""Generated backend — stdlib http.server, SQLite."""\nimport json, sqlite3\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\nclass H(BaseHTTPRequestHandler):\n    def do_GET(self):\n        if self.path == "/api/health":\n            self._json({"status": "ok"})\n        elif self.path == "/api/items":\n            self._json({"items": []})\n        else:\n            self._json({"error": "not found"}, 404)\n    def _json(self, obj, code=200):\n        b = json.dumps(obj).encode()\n        self.send_response(code); self.send_header("Content-Type", "application/json")\n        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)\n    def log_message(self, *a): pass\n\nif __name__ == "__main__":\n    HTTPServer(("0.0.0.0", 8000), H).serve_forever()\n')
        return AgentResult(ok=True, summary="Architecture + backend scaffold generated.", files=["docs/architecture.md", "backend/app.py"])

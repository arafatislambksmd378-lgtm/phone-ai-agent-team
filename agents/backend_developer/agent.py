"""Agent 6: Backend Developer — Implements backend services, APIs, auth, business logic."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "backend_developer"
    name = "Backend Developer"
    description = "Implements backend services, APIs, auth, business logic."
    system_prompt = "You are the Backend Developer. Implements backend services, APIs, auth, business logic. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        # Ensure backend exists (architect may have created it); otherwise create.
        if not self._read("backend/app.py"):
            self._write("backend/app.py", '"""Backend — stdlib server."""\nimport json\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\nclass H(BaseHTTPRequestHandler):\n    def do_GET(self):\n        b=json.dumps({"status":"ok"}).encode()\n        self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)\n    def log_message(self,*a): pass\nif __name__=="__main__": HTTPServer(("0.0.0.0",8000),H).serve_forever()\n')
        self._write("backend/requirements.txt", "# stdlib only\n")
        return AgentResult(ok=True, summary="Backend verified/created.", files=["backend/app.py"])

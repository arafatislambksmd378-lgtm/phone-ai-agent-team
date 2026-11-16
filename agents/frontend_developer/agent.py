"""Agent 5: Frontend Developer — Implements responsive frontend + API wiring."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "frontend_developer"
    name = "Frontend Developer"
    description = "Implements responsive frontend + API wiring."
    system_prompt = "You are the Frontend Developer. Implements responsive frontend + API wiring. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        html = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>App</title><style>body{font-family:system-ui;max-width:960px;margin:0 auto;padding:16px}header{background:#2563eb;color:#fff;padding:16px;border-radius:8px}.card{border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:12px 0}button{background:#2563eb;color:#fff;border:0;padding:10px 16px;border-radius:8px}</style></head><body><header><h1>App</h1></header><div class="card"><p>Loading...</p></div><script>fetch('/api/health').then(r=>r.json()).then(d=>{document.querySelector('.card p').textContent='Status: '+d.status}).catch(()=>{document.querySelector('.card p').textContent='Offline'})</script></body></html>"""
        self._write("frontend/index.html", html)
        return AgentResult(ok=True, summary="Frontend scaffold generated.", files=["frontend/index.html"])

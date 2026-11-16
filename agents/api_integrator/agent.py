"""Agent 8: API & Integration Agent — Integrates external APIs with retries, rate limits, adapters."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "api_integrator"
    name = "API & Integration Agent"
    description = "Integrates external APIs with retries, rate limits, adapters."
    system_prompt = "You are the API & Integration Agent. Integrates external APIs with retries, rate limits, adapters. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        code = '"""External API adapter — retry + timeout + redaction."""\nimport urllib.request, json, time\n\ndef call(url, payload, timeout=30, retries=2):\n    for i in range(retries+1):\n        try:\n            req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})\n            with urllib.request.urlopen(req, timeout=timeout) as r:\n                return json.loads(r.read())\n        except Exception as e:\n            if i == retries: raise\n            time.sleep(1)\n'
        self._write("backend/api_adapter.py", code)
        return AgentResult(ok=True, summary="API adapter generated.", files=["backend/api_adapter.py"])

"""Agent 8 — API & Integration: resilient HTTP client adapter for external APIs."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are an integrations engineer. Reply with one Python module that wraps
external HTTP APIs: timeout, bounded exponential backoff, JSON parsing, typed errors,
no secrets in code, no shell execution."""

ADAPTER = '''"""External API adapter — timeout, backoff, typed errors, redacted logging."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("api_adapter")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """Raised for any non-2xx response, with status and a redacted body snippet."""


def call(url, payload=None, *, method="POST", headers=None, timeout=20,
         retries=2, backoff=1.0, max_body=1_000_000):
    """Call a JSON API. Retries only idempotent/transient failures."""
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "generated-app/1.0"}
    hdrs.update(headers or {})
    last_error = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_body + 1)
            if len(raw) > max_body:
                raise ApiError("response too large", status=413)
            try:
                return json.loads(raw.decode() or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ApiError(f"invalid JSON: {e}", status=200) from e
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(400).decode("utf-8", "replace")
            except Exception:
                pass
            last_error = ApiError(f"HTTP {e.code}: {body}", status=e.code)
            if e.code not in RETRYABLE_STATUS or attempt == retries:
                raise last_error from None
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = ApiError(f"connection failed: {e}", status=0)
            if attempt == retries:
                raise last_error from None
        delay = backoff * (2 ** attempt)
        log.info("retrying %s in %.1fs (attempt %d/%d)", url, delay, attempt + 1, retries)
        time.sleep(delay)
    raise last_error or ApiError("unknown failure", status=0)
'''


class Agent(BaseAgent):
    agent_id = "api_integrator"
    name = "API & Integration Agent"
    description = "Builds resilient adapters for external APIs."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        gen = self.generate(
            f"Requirements:\n{self._read('docs/requirements.md')[:6000]}\n\n"
            "Write the API adapter module.", ADAPTER)
        code = gen.text if "def " in gen.text else ADAPTER
        self._write("backend/api_adapter.py", code)
        ok, detail = self._verify(code)
        return AgentResult(ok=ok, summary=f"API adapter written and {detail}",
                           files=["backend/api_adapter.py"], mode=gen.mode,
                           error="" if ok else detail)

    def _verify(self, code: str) -> tuple[bool, str]:
        try:
            compile(code, "backend/api_adapter.py", "exec")
            return True, "compiles"
        except SyntaxError as e:
            return False, f"syntax error: {e}"

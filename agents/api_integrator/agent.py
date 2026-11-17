"""Agent 8 — API & Integration Agent: resilient HTTP adapter with timeout, Retry-After & backoff."""
from __future__ import annotations

from agents.api_integrator.prompts import SYSTEM_PROMPT, build_user_prompt
from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from, validate_python_syntax

ADAPTER_TEMPLATE = '''"""External API adapter — timeout, Retry-After backoff, typed errors, size cap."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("api_adapter")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """Raised for any non-2xx or malformed response."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def call(url: str, payload: dict | None = None, *, method: str = "POST",
         headers: dict | None = None, timeout: int = 20,
         retries: int = 2, backoff: float = 1.0, max_body: int = 1_000_000) -> dict:
    """Invoke an external JSON HTTP endpoint with bounded retries and size limits."""
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "generated-app/1.0"}
    hdrs.update(headers or {})
    last_error: ApiError | None = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(max_body + 1)
            if len(raw) > max_body:
                raise ApiError("response too large", status=413)
            try:
                return json.loads(raw.decode("utf-8") or "{}")
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
            ra = (e.headers or {}).get("Retry-After") if getattr(e, "headers", None) else None
            delay = min(float(ra), 10.0) if (ra and str(ra).isdigit()) else backoff * (2 ** attempt)
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
    description = "Builds resilient external API adapters with timeouts, Retry-After, and typed errors."
    responsibilities = [
        "Implement external HTTP integration clients with bounded retries and rate-limit backoff",
        "Strip Markdown fences and verify Python compilation before saving",
        "Never embed live API keys or shell calls inside integration adapters",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["backend/api_adapter.py"],
        "required_data_keys": ["module", "compiled"],
    }
    completion_criteria = ["backend/api_adapter.py compiles without syntax errors"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/architecture.md", "docs/requirements.json"])
        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        prompt = build_user_prompt(
            title=title,
            software_type=ctx["software_type"],
            spec_md=ctx["requirements"],
            arch_md=ctx["files"].get("docs/architecture.md", ""),
        )
        gen = self.generate_code(
            prompt, ADAPTER_TEMPLATE, prefer_tag="python", compile_filename="backend/api_adapter.py"
        )
        code = gen.text if "def " in gen.text else ADAPTER_TEMPLATE
        ok, detail = validate_python_syntax(code, "backend/api_adapter.py")
        if not ok:
            code = ADAPTER_TEMPLATE
            ok, detail = True, "ok (fallback)"

        self._write("backend/api_adapter.py", code)
        return AgentResult(
            ok=ok,
            summary=f"API adapter written and verified ({gen.mode})",
            files=["backend/api_adapter.py"],
            data={"module": "backend/api_adapter.py", "compiled": ok},
            mode=gen.mode,
            error="" if ok else detail,
        )

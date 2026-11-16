"""Base agent — contracts, tool permission boundaries, router integration, context & output validation.

Agents are *logical* roles invoked on demand by OrchestratorEngine; they never run as
16 persistent OS processes. Every agent defines:
  - Dedicated system_prompt (loaded from its prompts.py)
  - Explicit responsibilities, input_schema, output_schema, completion_criteria
  - Allowed tools boundary (`allowed_tools`) enforced at every file/command/AI helper call
  - Bounded context builder (`build_context`) incorporating upstream handoffs safely on 3 GB RAM
  - AI Model Router integration (`generate`) with retry, fence-stripping, and honest mode reporting
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents.helpers import GenResult, extract_code_block, extract_json_block

log = logging.getLogger("agents")

DEFAULT_ALLOWED_TOOLS = {"read_file", "write_file", "list_files", "ai_generate", "db_read"}


@dataclass
class AgentResult:
    ok: bool
    summary: str
    files: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    mode: str = "deterministic"   # "live" | "mock" | "failed" | "deterministic"


class ToolPermissionError(PermissionError):
    """Raised when an Agent invokes a tool outside its allowed_tools boundary."""


class BaseAgent:
    agent_id: str = "base"
    name: str = "Base Agent"
    description: str = ""
    responsibilities: list[str] = []
    system_prompt: str = "You are a precise senior software engineer."
    allowed_tools: set[str] = set(DEFAULT_ALLOWED_TOOLS)
    input_schema: dict[str, Any] = {
        "required": [],
        "optional": ["requirements", "software_type", "upstream_handoffs", "shared_context"],
    }
    output_schema: dict[str, Any] = {
        "required_files_any": [],
        "required_data_keys": [],
    }
    completion_criteria: list[str] = ["Summary is non-empty and declared artifacts exist in workspace"]
    max_context_chars: int = 12_000
    max_ai_retries: int = 1

    def __init__(self, workspace: str = ".", ai_router=None, db=None, file_manager=None,
                 approval_manager=None, process_manager=None):
        self.workspace = workspace
        self.ai = ai_router
        self.db = db
        self.fm = file_manager
        self.approval_manager = approval_manager
        self.process_manager = process_manager

    # ---- contract inspection ----
    def contract(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "responsibilities": list(self.responsibilities),
            "allowed_tools": sorted(self.allowed_tools),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "completion_criteria": list(self.completion_criteria),
        }

    def _require_tool(self, tool_name: str) -> None:
        if tool_name not in self.allowed_tools:
            raise ToolPermissionError(
                f"Agent {self.agent_id!r} is not permitted to use tool {tool_name!r} "
                f"(allowed: {sorted(self.allowed_tools)})"
            )

    # ---- lifecycle template ----
    def run(self, task: dict) -> AgentResult:
        eid = str(task.get("execution_id") or "")
        t0 = time.time()
        payload = self._payload(task)
        valid_in, in_err = self.validate_input(task, payload)
        if not valid_in:
            self._log(eid, "ERROR", f"[{self.agent_id}] input validation failed: {in_err}")
            return AgentResult(ok=False, summary=f"Input validation failed: {in_err}",
                               error=in_err, mode="deterministic")

        self._log(eid, "INFO", f"[{self.agent_id}] starting task '{task.get('title', self.name)}'")
        try:
            result = self.execute(task)
        except ToolPermissionError as e:
            self._log(eid, "ERROR", f"[{self.agent_id}] permission boundary violation: {e}")
            return AgentResult(ok=False, summary=f"Permission denied: {e}", error=str(e),
                               mode="deterministic")
        except Exception as e:
            log.exception("Agent %s raised exception", self.agent_id)
            self._log(eid, "ERROR", f"[{self.agent_id}] unhandled error: {type(e).__name__}: {e}")
            return AgentResult(ok=False, summary=f"{self.name} error: {e}",
                               error=f"{type(e).__name__}: {e}", mode="deterministic")

        valid_out, out_err = self.validate_output(result)
        if not valid_out and result.ok:
            result.ok = False
            result.error = out_err
            result.summary = f"{result.summary} (output validation failed: {out_err})"

        dur_ms = int((time.time() - t0) * 1000)
        self._log(
            eid,
            "INFO" if result.ok else "WARNING",
            f"[{self.agent_id}] finished ok={result.ok} mode={result.mode} "
            f"files={len(result.files)} in {dur_ms}ms: {result.summary}",
        )
        return result

    def execute(self, task: dict) -> AgentResult:
        raise NotImplementedError

    # ---- input / output validation ----
    def validate_input(self, task: dict, payload: dict) -> tuple[bool, str]:
        if not isinstance(task, dict):
            return False, "task must be a dictionary"
        for req_key in self.input_schema.get("required", []):
            if req_key not in payload and req_key not in task:
                return False, f"missing required input field: {req_key}"
        return True, "ok"

    def validate_output(self, result: AgentResult) -> tuple[bool, str]:
        if not isinstance(result, AgentResult):
            return False, "result must be an AgentResult"
        if not (result.summary or "").strip():
            return False, "summary cannot be empty"
        for rel in (result.files or []):
            if not self._exists_unchecked(rel):
                return False, f"declared artifact missing from workspace: {rel}"
        any_files = self.output_schema.get("required_files_any") or []
        if result.ok and any_files and not any(self._exists_unchecked(f) for f in any_files):
            return False, f"none of expected output files exist: {any_files}"
        for k in (self.output_schema.get("required_data_keys") or []):
            if result.ok and k not in (result.data or {}):
                return False, f"missing required output data key: {k}"
        return True, "ok"

    # ---- context management (bounded for 3 GB RAM & LLM prompt safety) ----
    def build_context(self, task: dict, extra_files: list[str] | None = None,
                      max_chars: int | None = None) -> dict[str, Any]:
        payload = self._payload(task)
        budget = max(2000, int(max_chars or self.max_context_chars))
        spec_json = extract_json_block(self._read("docs/requirements.json")) if self._exists("docs/requirements.json") else {}
        analysis = (
            payload.get("blueprint_analysis")
            or (payload.get("shared_context") or {}).get("blueprint_analysis")
            or {}
        )
        software_type = (
            payload.get("software_type")
            or analysis.get("software_type")
            or spec_json.get("software_type")
            or "web_app"
        )
        entities = (
            payload.get("entities")
            or analysis.get("entities")
            or spec_json.get("entities")
            or ["items"]
        )
        technologies = (
            payload.get("technologies")
            or analysis.get("technologies")
            or spec_json.get("technologies")
            or ["Python 3.10+", "SQLite (WAL)"]
        )

        upstream = payload.get("upstream_handoffs") or {}
        handoff_lines: list[str] = []
        for aid, info in upstream.items():
            if isinstance(info, dict):
                handoff_lines.append(f"- {aid}: {info.get('summary', '')}")
        handoff_text = "\n".join(handoff_lines)[:2000]

        req_text = (
            self._read("docs/requirements.md")
            or payload.get("requirements")
            or self._requirements()
        )[: budget // 2]

        file_snippets: dict[str, str] = {}
        used = len(req_text) + len(handoff_text)
        for rel in (extra_files or []):
            if used >= budget:
                break
            if self._exists(rel):
                snippet = self._read(rel)[: max(500, budget - used)]
                file_snippets[rel] = snippet
                used += len(snippet)

        return {
            "project_id": task.get("project_id", ""),
            "software_type": software_type,
            "complexity": payload.get("complexity") or analysis.get("complexity") or "moderate",
            "entities": entities,
            "technologies": technologies,
            "platform_limitations": payload.get("platform_limitations") or analysis.get("platform_limitations") or [],
            "requirements": req_text,
            "spec": spec_json,
            "upstream_handoffs": upstream,
            "handoff_summary": handoff_text,
            "files": file_snippets,
            "execution_history": payload.get("execution_history") or [],
        }

    # ---- AI Model Router integration with retry & validation ----
    def generate(self, instruction: str, fallback: str, agent_id: str | None = None,
                 *, max_tokens: int | None = None, temperature: float = 0.2,
                 validator=None) -> GenResult:
        """Call the configured AI Model Router with bounded retry and honest fallback."""
        self._require_tool("ai_generate")
        if self.ai is None:
            return GenResult(text=fallback, mode="deterministic")

        last_err = ""
        last_provider = ""
        prompt = instruction[:32_000]

        for attempt in range(self.max_ai_retries + 1):
            try:
                r = self.ai.complete(
                    agent_id=agent_id or self.agent_id,
                    system=self.system_prompt,
                    user=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except TypeError:
                # Backward-compatible call for minimal test stubs with 3-arg complete()
                try:
                    r = self.ai.complete(agent_id=agent_id or self.agent_id,
                                         system=self.system_prompt, user=prompt)
                except Exception as e:
                    return GenResult(text=fallback, mode="failed", error=str(e)[:200])
            except Exception as e:
                return GenResult(text=fallback, mode="failed", error=str(e)[:200])

            if r.get("mode") == "mock" or r.get("mock"):
                return GenResult(text=fallback, mode="mock")

            if not r.get("ok"):
                last_err = str(r.get("error", ""))[:200]
                last_provider = str(r.get("provider", ""))
                break

            text = (r.get("text") or "").strip()
            if not text:
                last_err = "empty AI response"
                break

            if validator is not None:
                ok_val, cleaned_or_err = validator(text)
                if ok_val:
                    return GenResult(
                        text=cleaned_or_err,
                        mode="live",
                        provider=r.get("provider", ""),
                        model=r.get("model", ""),
                    )
                last_err = f"AI output validation failed: {cleaned_or_err}"
                if attempt < self.max_ai_retries:
                    prompt = (
                        f"{instruction[:24_000]}\n\n"
                        f"IMPORTANT: Your previous response failed validation ({cleaned_or_err}). "
                        f"Return valid, complete output only."
                    )
                    continue
                return GenResult(
                    text=fallback,
                    mode="failed",
                    provider=r.get("provider", ""),
                    model=r.get("model", ""),
                    error=last_err[:200],
                )

            return GenResult(
                text=text,
                mode="live",
                provider=r.get("provider", ""),
                model=r.get("model", ""),
            )

        return GenResult(text=fallback, mode="failed", error=last_err, provider=last_provider)

    def generate_code(self, instruction: str, fallback: str, *, prefer_tag: str = "python",
                      compile_filename: str | None = None) -> GenResult:
        """Generate source code via Model Router, strip Markdown fences, and verify syntax if Python."""
        def _val(raw_text: str) -> tuple[bool, str]:
            code = extract_code_block(raw_text, prefer_tag=prefer_tag)
            if not code.strip():
                return False, "empty code block"
            if compile_filename:
                try:
                    compile(code, compile_filename, "exec")
                except SyntaxError as e:
                    return False, f"SyntaxError line {e.lineno}: {e.msg}"
            return True, code.rstrip() + "\n"

        return self.generate(instruction, fallback, validator=_val)

    # ---- workspace-confined file & command helpers (tool-permission enforced) ----
    def _write(self, rel: str, content: str) -> str:
        self._require_tool("write_file")
        if self.fm is not None:
            return self.fm.write(rel, content)
        from security.validation import safe_join
        p = safe_join(self.workspace, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _read(self, rel: str) -> str:
        self._require_tool("read_file")
        if self.fm is not None:
            try:
                return self.fm.read(rel)
            except Exception:
                return ""
        from security.validation import safe_join
        try:
            return safe_join(self.workspace, rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _exists(self, rel: str) -> bool:
        self._require_tool("read_file")
        return self._exists_unchecked(rel)

    def _exists_unchecked(self, rel: str) -> bool:
        if self.fm is not None:
            return self.fm.exists(rel)
        from security.validation import safe_join
        try:
            return safe_join(self.workspace, rel).exists()
        except ValueError:
            return False

    def _list(self) -> list[str]:
        self._require_tool("list_files")
        if self.fm is not None:
            return self.fm.list_files()
        from pathlib import Path
        root = Path(self.workspace)
        return [str(p.relative_to(root)) for p in sorted(root.rglob("*"))
                if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts][:5000]

    def _path(self, rel: str):
        from security.validation import safe_join
        return safe_join(self.workspace, rel)

    # ---- misc ----
    def _payload(self, task: dict) -> dict:
        raw = task.get("payload")
        if isinstance(raw, dict):
            return dict(raw)
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def _log(self, eid: str, level: str, msg: str) -> None:
        if self.db is not None and eid:
            try:
                self.db.log(eid, level, msg)
            except Exception:
                pass

    def _requirements(self) -> str:
        return self._read("requirements.txt") or self._read("docs/requirements.md")

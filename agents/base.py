"""Base agent — common interface: run(task) -> AgentResult.

Agents are *logical* workers invoked on demand; they never spawn processes of
their own. Every agent uses `self.generate()` which calls the configured AI
provider and falls back to deterministic output, recording which mode was used.
"""
from __future__ import annotations
import json

from dataclasses import dataclass, field

from agents.helpers import GenResult


@dataclass
class AgentResult:
    ok: bool
    summary: str
    files: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    error: str = ""
    mode: str = "deterministic"   # "live" | "mock" | "deterministic"


class BaseAgent:
    agent_id: str = "base"
    name: str = "Base Agent"
    description: str = ""
    system_prompt: str = "You are a precise senior software engineer."

    def __init__(self, workspace: str = ".", ai_router=None, db=None, file_manager=None):
        self.workspace = workspace
        self.ai = ai_router
        self.db = db
        self.fm = file_manager

    # ---- contract ----
    def run(self, task: dict) -> AgentResult:
        raise NotImplementedError

    # ---- AI with honest fallback ----
    def generate(self, instruction: str, fallback: str, agent_id: str | None = None) -> GenResult:
        """Ask the router; fall back deterministically. Never raises."""
        if self.ai is None:
            return GenResult(text=fallback, mode="deterministic")
        try:
            r = self.ai.complete(agent_id=agent_id or self.agent_id,
                                 system=self.system_prompt, user=instruction)
        except Exception as e:  # a broken router must not break the pipeline
            return GenResult(text=fallback, mode="failed", error=str(e)[:200])
        if r.get("mode") == "mock" or r.get("mock"):
            return GenResult(text=fallback, mode="mock")
        if not r.get("ok"):
            return GenResult(text=fallback, mode="failed",
                             error=str(r.get("error", ""))[:200], provider=str(r.get("provider", "")))
        text = (r.get("text") or "").strip()
        if not text:
            return GenResult(text=fallback, mode="failed", error="empty AI response")
        return GenResult(text=text, mode="live", provider=r.get("provider", ""), model=r.get("model", ""))

    # ---- file helpers (workspace-confined) ----
    def _write(self, rel: str, content: str) -> str:
        if self.fm is not None:
            return self.fm.write(rel, content)
        from pathlib import Path
        from security.validation import safe_join
        p = safe_join(self.workspace, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _read(self, rel: str) -> str:
        if self.fm is not None:
            try:
                return self.fm.read(rel)
            except Exception:
                return ""
        from pathlib import Path
        from security.validation import safe_join
        try:
            return safe_join(self.workspace, rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _exists(self, rel: str) -> bool:
        if self.fm is not None:
            return self.fm.exists(rel)
        from security.validation import safe_join
        try:
            return safe_join(self.workspace, rel).exists()
        except ValueError:
            return False

    def _list(self) -> list[str]:
        if self.fm is not None:
            return self.fm.list_files()
        from pathlib import Path
        root = Path(self.workspace)
        return [str(p.relative_to(root)) for p in sorted(root.rglob("*"))
                if p.is_file() and ".git" not in p.parts][:5000]

    # ---- misc ----
    def _payload(self, task: dict) -> dict:
        try:
            return json.loads(task.get("payload") or "{}")
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

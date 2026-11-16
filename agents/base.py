"""Base agent — common interface: run(task) -> AgentResult. Logical workers, not processes."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field

@dataclass
class AgentResult:
    ok: bool
    summary: str
    files: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    error: str = ""

class BaseAgent:
    agent_id: str = "base"
    name: str = "Base Agent"
    description: str = ""
    system_prompt: str = "You are a helpful software engineering agent."

    def __init__(self, workspace: str = ".", ai_router=None, db=None, file_manager=None):
        self.workspace = workspace
        self.ai = ai_router
        self.db = db
        self.fm = file_manager

    def run(self, task: dict) -> AgentResult:
        raise NotImplementedError

    def _ai_complete(self, prompt: str, task: dict | None = None) -> str:
        """Ask AI router if available; fall back to deterministic local generation."""
        if self.ai is not None:
            try:
                r = self.ai.complete(agent_id=self.agent_id, system=self.system_prompt, user=prompt)
                if r.get("ok"):
                    return r.get("text", "")
            except Exception:
                pass
        return ""

    def _write(self, rel: str, content: str) -> str:
        if self.fm is not None:
            return self.fm.write(rel, content)
        from pathlib import Path
        p = Path(self.workspace) / rel
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
        try:
            return (Path(self.workspace) / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _payload(self, task: dict) -> dict:
        try:
            return json.loads(task.get("payload") or "{}")
        except Exception:
            return {}

    def _log(self, eid: str, level: str, msg: str) -> None:
        if self.db is not None and eid:
            try:
                self.db.log(eid, level, msg)
            except Exception:
                pass

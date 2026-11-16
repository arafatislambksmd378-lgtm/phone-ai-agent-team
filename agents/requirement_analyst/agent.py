"""Agent 2 — Requirement Analyst: turns raw blueprints into a structured spec."""
from __future__ import annotations
import json
import re

from agents.base import AgentResult, BaseAgent
from agents.helpers import feature_lines, title_from

SYSTEM = """You are a senior requirement analyst. Convert the supplied blueprint into a
structured specification. Reply in Markdown only, with sections: Overview, Functional
Requirements (numbered), Non-Functional Requirements, Data Entities, Out of Scope,
Ambiguities. Be concrete and testable; never invent external services."""


class Agent(BaseAgent):
    agent_id = "requirement_analyst"
    name = "Requirement Analyst"
    description = "Extracts and structures functional/non-functional requirements."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        raw = payload.get("requirements") or self._requirements() or "Build a small web application."
        extract = self._extract_project_uploads()
        corpus = (raw + "\n" + extract)[:120_000]
        fallback = self._fallback(corpus)
        gen = self.generate(
            f"Blueprints and notes:\n\n{corpus[:12000]}\n\nProduce the structured specification.",
            fallback)
        self._write("docs/requirements.md", gen.text)
        frs = [l.strip("- 0123456789.") for l in gen.text.splitlines()
               if re.match(r"^\s*(\d+[.)]|[-*])\s+\S", l)][:60]
        spec = {
            "project_title": title_from(corpus),
            "functional_requirements": frs,
            "source_chars": len(corpus),
            "mode": gen.mode,
            "provider": gen.provider,
        }
        self._write("docs/requirements.json", json.dumps(spec, indent=2))
        return AgentResult(
            ok=bool(frs) or bool(gen.text.strip()),
            summary=f"{len(frs)} requirements extracted ({gen.mode})",
            files=["docs/requirements.md", "docs/requirements.json"],
            data=spec, mode=gen.mode, error=gen.error)

    def _extract_project_uploads(self) -> str:
        parts = []
        for rel in self._list():
            low = rel.lower()
            if low.startswith(("uploads/", "extracted/")) and low.endswith((".txt", ".md", ".json", ".csv")):
                body = self._read(rel)[:20_000]
                if body:
                    parts.append(f"--- {rel} ---\n{body}")
            if len(parts) >= 8:
                break
        return "\n\n".join(parts)

    def _fallback(self, corpus: str) -> str:
        feats = feature_lines(corpus, 10) or ["Core CRUD workflow", "Persistent storage", "Basic authentication"]
        title = title_from(corpus)
        lines = [f"# Specification — {title}", "", "## Overview", corpus.strip()[:800] or title, "",
                 "## Functional Requirements", ""]
        lines += [f"{i}. The system shall support: {f}" for i, f in enumerate(feats, 1)]
        lines += ["", "## Non-Functional Requirements", "",
                  "1. Runs on Android/Termux with 3 GB RAM and SQLite — no external database.",
                  "2. All inputs validated; uploads treated as untrusted.",
                  "3. Secrets never written to the workspace or logs.",
                  "4. Response times under 500 ms for local operations.", "",
                  "## Data Entities", "", "- items (id, title, notes, created_at)", "",
                  "## Out of Scope", "", "- Cloud deployment, payments, third-party accounts.",
                  "", "## Ambiguities", "", "- None detected in the supplied material."]
        return "\n".join(lines)

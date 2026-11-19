"""Agent 2 — Requirement Analyst: extracts structured specs from text, PDF, DOCX, CSV, JSON & ZIP."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.base import AgentResult, BaseAgent
from agents.helpers import feature_lines, primary_entity_from, title_from
from agents.requirement_analyst.prompts import SYSTEM_PROMPT, build_user_prompt
from execution.document_processor import extract_directory_corpus


class Agent(BaseAgent):
    agent_id = "requirement_analyst"
    name = "Requirement Analyst"
    description = "Extracts functional/non-functional requirements, entities, and tech stack from blueprints and uploads."
    responsibilities = [
        "Extract clean text from TXT, MD, JSON, CSV, PDF, DOCX, source files, and extracted ZIPs",
        "Structure functional and non-functional requirements into testable items",
        "Identify domain entities, software category, and target technology stack",
        "Produce docs/requirements.md and machine-readable docs/requirements.json for downstream agents",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["docs/requirements.md", "docs/requirements.json"],
        "required_data_keys": ["project_title", "software_type", "functional_requirements", "entities"],
    }
    completion_criteria = ["At least one requirement extracted and written to docs/requirements.md and docs/requirements.json"]

    def execute(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        ctx = self.build_context(task)
        raw = payload.get("requirements") or self._requirements() or "Build a small web application."
        extracted_docs = self._extract_project_uploads()
        corpus = (raw + ("\n\n" + extracted_docs if extracted_docs else "")).strip()[:120_000]

        software_type = ctx["software_type"]
        entities_plural, entity_singular = primary_entity_from(ctx["entities"], corpus)
        entities = [entities_plural] + [e for e in ctx["entities"] if e != entities_plural]
        technologies = ctx["technologies"]

        fallback = self._fallback(corpus, software_type, entities, technologies, ctx["platform_limitations"])
        prompt = build_user_prompt(corpus, software_type, entities, technologies)
        gen = self.generate(prompt, fallback)

        md_text = gen.text if len(gen.text.strip()) >= 40 else fallback
        self._write("docs/requirements.md", md_text)

        frs = [
            re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", l).strip()
            for l in md_text.splitlines()
            if re.match(r"^\s*(\d+[.)]|[-*])\s+\S", l)
        ][:60]
        if not frs:
            frs = feature_lines(corpus, 10) or [f"Manage {entities_plural} records"]

        nfrs = [f for f in frs if any(k in f.lower() for k in ("ram", "termux", "security", "latency", "sqlite", "timeout"))][:12]
        if not nfrs:
            nfrs = [
                "Operate reliably within Android 14 / Termux 3 GB RAM constraints.",
                "Validate all inputs and never expose secrets or credentials.",
            ]

        spec = {
            "project_title": title_from(corpus),
            "software_type": software_type,
            "complexity": ctx["complexity"],
            "entities": entities[:6],
            "primary_entity": entities_plural,
            "primary_entity_singular": entity_singular,
            "technologies": technologies,
            "functional_requirements": frs[:30],
            "non_functional_requirements": nfrs,
            "platform_limitations": ctx["platform_limitations"],
            "source_chars": len(corpus),
            "mode": gen.mode,
            "provider": gen.provider,
        }
        self._write("docs/requirements.json", json.dumps(spec, indent=2))
        return AgentResult(
            ok=bool(frs) or bool(md_text.strip()),
            summary=f"{len(frs)} requirements extracted for {software_type} ({gen.mode})",
            files=["docs/requirements.md", "docs/requirements.json"],
            data=spec,
            mode=gen.mode,
            error=gen.error,
        )

    def _extract_project_uploads(self) -> str:
        ws = Path(self.workspace)
        chunks: list[str] = []
        for sub in ("uploads", "extracted"):
            d = ws / sub
            if d.exists():
                text = extract_directory_corpus(d, max_files=15, max_chars=45_000)
                if text:
                    chunks.append(text)
        return "\n\n".join(chunks)

    @staticmethod
    def _fallback(corpus: str, software_type: str, entities: list[str],
                  technologies: list[str], limitations: list[str]) -> str:
        feats = feature_lines(corpus, 12) or [
            f"Create, list, inspect, and remove {entities[0]}",
            "Validate inputs and report clear error messages",
            "Persist state cleanly with crash-safe operations",
        ]
        title = title_from(corpus)
        lines = [
            f"# Specification — {title}",
            "",
            "## 1. Overview & Software Type",
            f"- **Category:** `{software_type}`",
            f"- **Summary:** {corpus.strip()[:900] or title}",
            "",
            "## 2. Functional Requirements",
            "",
            *[f"{i}. The system shall support: {f}" for i, f in enumerate(feats, 1)],
            "",
            "## 3. Non-Functional Requirements",
            "",
            "1. Runs cleanly on Android 14 / Termux (3 GB RAM) without heavy background daemons.",
            "2. All external inputs are validated and size-bounded.",
            "3. Secrets and credentials are never hardcoded or logged.",
            "4. Deterministic automated tests verify core behavior.",
            "",
            "## 4. Domain Data Entities & Attributes",
            "",
            *[f"- `{e}` (`id`, `title`/`name`, `notes`/`details`, `status`, `created_at`)" for e in entities[:4]],
            "",
            "## 5. Target Technology Stack",
            "",
            *[f"- {t}" for t in technologies],
            "",
            "## 6. Out of Scope & Platform Constraints",
            "",
            *([f"- {lim}" for lim in limitations] if limitations else ["- Unapproved cloud deployments or paid third-party APIs."]),
            "",
            "## 7. Open Ambiguities & Safe Assumptions",
            "",
            "- Defaults to lightweight Python standard library components compatible with Termux.",
        ]
        return "\n".join(lines) + "\n"

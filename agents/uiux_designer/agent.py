"""Agent 4: UI/UX Designer — Designs screens, journeys, design system, frontend guidance."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "uiux_designer"
    name = "UI/UX Designer"
    description = "Designs screens, journeys, design system, frontend guidance."
    system_prompt = "You are the UI/UX Designer. Designs screens, journeys, design system, frontend guidance. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        spec = self._read("docs/requirements.json") or "{}"
        try:
            title = json.loads(spec).get("project_title", "App")
        except Exception:
            title = "App"
        doc = f"""# UI/UX Design — {title}\n\n## Design system\n- Colors: primary #2563eb, bg #ffffff, text #111827\n- Font: system-ui (mobile-friendly)\n- Spacing: 8pt grid, max-width 960px\n\n## Screens\n1. Home — hero + CTA\n2. List — items grid\n3. Detail — item view\n4. Form — create/edit\n\n## User journey\nHome -> List -> Detail -> Form -> Success toast\n\n## States\n- Loading skeleton, empty state, error banner, offline notice\n"""
        self._write("docs/uiux.md", doc)
        return AgentResult(ok=True, summary="UI/UX spec generated.", files=["docs/uiux.md"])

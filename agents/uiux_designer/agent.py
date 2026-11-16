"""Agent 4 — UI/UX Designer: screens, flows, design system."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from

SYSTEM = """You are a UI/UX designer. Produce a Markdown design spec:
design tokens, screen inventory, navigation model, component states (loading/empty/
error/offline) and accessibility notes. Assume a small phone screen, one hand, and a
slow connection. No images, no external design tools."""


class Agent(BaseAgent):
    agent_id = "uiux_designer"
    name = "UI/UX Designer"
    description = "Designs screens, journeys, design system and frontend guidance."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        title = title_from(self._read("docs/requirements.md"))
        fallback = f"""# UI/UX — {title}

## Design tokens
- Colour: primary `#2563eb`, surface `#ffffff`, text `#111827`, danger `#dc2626`
- Radius: 8 px · Spacing: 8 pt grid · Font: `system-ui`
- Breakpoints: ≤480 px phone, ≤960 px tablet, else desktop

## Screens
| Screen | Purpose | Key elements |
|---|---|---|
| Home | Overview + primary action | Header, list, FAB |
| Item detail | Inspect one item | Title, notes, delete |
| New item | Create | Title field, notes, save |
| Settings | Local preferences | Theme, data reset |

## Navigation
Home → Detail → (back) Home; Home → New → Home on success.

## Component states
- **Loading**: skeleton rows, no spinners longer than 300 ms.
- **Empty**: friendly copy plus a primary action button.
- **Error**: inline banner with retry, never a modal.
- **Offline**: persistent bar; queue writes locally.

## Accessibility
- 44 px minimum touch targets, contrast ≥ 4.5:1, visible focus rings,
  labels wired to inputs, `aria-live` for async results.

## Responsiveness
Single column under 480 px, two columns above 768 px, max content width 960 px.
"""
        gen = self.generate(
            f"Requirements:\n{self._read('docs/requirements.md')[:8000]}\n\nWrite the UI/UX spec.",
            fallback)
        self._write("docs/uiux.md", gen.text)
        return AgentResult(ok=True, summary=f"UI/UX spec written ({gen.mode})",
                           files=["docs/uiux.md"], mode=gen.mode, error=gen.error)

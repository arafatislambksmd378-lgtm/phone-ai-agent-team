"""Agent 4 — UI/UX Designer: category-aware user flows, screens, states, and design tokens."""
from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from agents.helpers import title_from
from agents.uiux_designer.prompts import SYSTEM_PROMPT, build_user_prompt


class Agent(BaseAgent):
    agent_id = "uiux_designer"
    name = "UI/UX Designer"
    description = "Designs screens, user flows, design tokens, component states, and accessibility specs."
    responsibilities = [
        "Define color, spacing, and typography design tokens suited to the target UI surface",
        "Specify screens/views, navigation journeys, and interactive states (loading/empty/error/offline)",
        "Enforce accessibility requirements (44px touch targets, contrast >= 4.5:1, keyboard/screen-reader support)",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["docs/uiux.md"],
        "required_data_keys": ["surface", "screens"],
    }
    completion_criteria = ["docs/uiux.md created with tokens, screens, states, and accessibility notes"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/architecture.md", "docs/requirements.json"])
        title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
        software_type = ctx["software_type"]
        surface = {
            "android_app": "Android Material 3 Mobile UI",
            "desktop_app": "Desktop Windowed MVC Layout (Tkinter)",
        }.get(software_type, "Responsive Mobile-First Web SPA")

        fallback = self._fallback(title, surface, ctx["entities"])
        prompt = build_user_prompt(
            title=title,
            software_type=surface,
            entities=ctx["entities"],
            spec_md=ctx["requirements"],
            arch_md=ctx["files"].get("docs/architecture.md", ""),
        )
        gen = self.generate(prompt, fallback)
        self._write("docs/uiux.md", gen.text if len(gen.text.strip()) >= 40 else fallback)

        screens = ["Home / List View", "Create / Edit Form", "Detail / Status View"]
        return AgentResult(
            ok=True,
            summary=f"UI/UX spec written for {surface} ({gen.mode})",
            files=["docs/uiux.md"],
            data={"surface": surface, "screens": screens, "software_type": software_type},
            mode=gen.mode,
            error=gen.error,
        )

    @staticmethod
    def _fallback(title: str, surface: str, entities: list[str]) -> str:
        ent = entities[0] if entities else "items"
        return f"""# UI/UX Specification — {title}

## Target Surface
- **Platform:** {surface}

## Design Tokens
- **Palette:** primary `#2563eb`, surface `#ffffff`, background `#f8fafc`, text `#111827`, muted `#6b7280`, danger `#dc2626`
- **Geometry:** 8 px border radius · 8 pt spacing grid · system sans-serif font stack
- **Breakpoints:** `<= 480px` compact phone, `<= 960px` tablet, `> 960px` desktop

## Screen Inventory (`{ent}`)
| Screen / View | Purpose | Key Controls |
|---|---|---|
| Primary List | Browse & filter `{ent}` | Header bar, search/add bar, status toggle, delete button |
| Creation Panel | Add new `{ent}` entry | Validated title & notes inputs, primary submit button |
| Feedback Banner | Non-blocking status/errors | `role="alert"` inline banner + `aria-live="polite"` counter |

## Component States
- **Loading:** Non-shifting status text, submit button disabled during in-flight request.
- **Empty:** Actionable empty-state guidance prompting the user to add the first record.
- **Error:** Inline high-contrast alert banner with retry capability (no blocking modals).
- **Offline:** Clear offline status indicator preserving typed form input.

## Accessibility
- Minimum 44 px touch targets on primary actions, WCAG AA contrast (`>= 4.5:1`), explicit `<label>` bindings, and `aria-live` announcements.
"""

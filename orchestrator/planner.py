"""Task planner — requirements → ordered 16-agent DAG with explicit dependencies."""
from __future__ import annotations
from memory.database import Database

# (agent_id, title, priority) — all 16 agents participate in a full run.
PIPELINE: list[tuple[str, str, int]] = [
    ("chief_orchestrator", "Coordinate pipeline", 5),
    ("requirement_analyst", "Analyze requirements", 10),
    ("software_architect", "Design architecture", 20),
    ("uiux_designer", "Design UI/UX", 30),
    ("database_engineer", "Design database", 40),
    ("backend_developer", "Implement backend", 50),
    ("frontend_developer", "Implement frontend", 60),
    ("api_integrator", "Integrate APIs", 70),
    ("security_engineer", "Security review", 80),
    ("qa_tester", "Test the build", 90),
    ("debugger", "Repair failures", 95),
    ("code_reviewer", "Review & optimize", 100),
    ("devops", "Prepare deployment", 110),
    ("documentation", "Write documentation", 120),
    ("release_manager", "Prepare release", 130),
    ("workspace_executor", "Verify workspace", 140),
]

# Quick mode keeps the first-3-agent acceptance path plus the essentials.
QUICK_AGENTS = ("requirement_analyst", "software_architect", "uiux_designer",
                "backend_developer", "release_manager")


class TaskPlanner:
    def __init__(self, db: Database):
        self.db = db

    def plan(self, project_id: str, requirements: str = "", quick: bool = False) -> list[str]:
        """Create the pipeline sequentially (each step depends on the previous one)."""
        if quick:
            steps = [s for s in PIPELINE if s[0] in QUICK_AGENTS]
        else:
            steps = PIPELINE
        ids: list[str] = []
        prev: str | None = None
        for agent_id, title, prio in steps:
            tid = self.db.create_task(project_id, agent_id, title,
                                      payload={"requirements": (requirements or "")[:4000]},
                                      priority=prio,
                                      depends_on=[prev] if prev else None)
            ids.append(tid)
            prev = tid
        return ids

    def plan_custom(self, project_id: str, steps: list[dict]) -> list[str]:
        """Ad-hoc pipeline (Telegram `/run`), still dependency-linked."""
        ids: list[str] = []
        prev: str | None = None
        for i, s in enumerate(steps):
            tid = self.db.create_task(project_id, s.get("agent_id", "backend_developer"),
                                      s.get("title", f"Step {i}"),
                                      payload=s.get("payload", {}),
                                      priority=s.get("priority", 100 + i),
                                      depends_on=[prev] if prev else None)
            ids.append(tid)
            prev = tid
        return ids

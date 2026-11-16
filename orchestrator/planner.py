"""Task planner — turns requirements into an ordered agent task DAG."""
from __future__ import annotations
from memory.database import Database

# Standard pipeline: (agent_id, title, priority)
PIPELINE: list[tuple[str, str, int]] = [
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
]

class TaskPlanner:
    def __init__(self, db: Database):
        self.db = db

    def plan(self, project_id: str, requirements: str = "", quick: bool = False) -> list[str]:
        """Create tasks in dependency order. Returns task ids."""
        steps = PIPELINE[:5] if quick else PIPELINE
        ids: list[str] = []
        prev: str | None = None
        for agent_id, title, prio in steps:
            tid = self.db.create_task(project_id, agent_id, title,
                                      payload={"requirements": requirements[:4000]},
                                      priority=prio,
                                      depends_on=[prev] if prev else None)
            ids.append(tid)
            prev = tid
        return ids

    def plan_custom(self, project_id: str, steps: list[dict]) -> list[str]:
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

"""Agent registry — maps agent_id -> class. Agents are logical workers invoked on demand."""
from __future__ import annotations
import importlib

AGENT_MODULES: dict[str, str] = {
    "chief_orchestrator": "agents.chief_orchestrator.agent",
    "requirement_analyst": "agents.requirement_analyst.agent",
    "software_architect": "agents.software_architect.agent",
    "uiux_designer": "agents.uiux_designer.agent",
    "frontend_developer": "agents.frontend_developer.agent",
    "backend_developer": "agents.backend_developer.agent",
    "database_engineer": "agents.database_engineer.agent",
    "api_integrator": "agents.api_integrator.agent",
    "security_engineer": "agents.security_engineer.agent",
    "qa_tester": "agents.qa_tester.agent",
    "debugger": "agents.debugger.agent",
    "code_reviewer": "agents.code_reviewer.agent",
    "devops": "agents.devops.agent",
    "documentation": "agents.documentation.agent",
    "release_manager": "agents.release_manager.agent",
    "workspace_executor": "agents.workspace_executor.agent",
}

AGENT_NAMES: dict[str, str] = {
    "chief_orchestrator": "Chief AI Orchestrator",
    "requirement_analyst": "Requirement Analyst",
    "software_architect": "Software Architect",
    "uiux_designer": "UI/UX Designer",
    "frontend_developer": "Frontend Developer",
    "backend_developer": "Backend Developer",
    "database_engineer": "Database Engineer",
    "api_integrator": "API & Integration Agent",
    "security_engineer": "Security Engineer",
    "qa_tester": "QA & Testing Agent",
    "debugger": "Debugging & Code Repair Agent",
    "code_reviewer": "Code Review & Optimization Agent",
    "devops": "DevOps & Deployment Agent",
    "documentation": "Technical Documentation Agent",
    "release_manager": "Release Manager",
    "workspace_executor": "Workspace & Tool Execution Agent",
}

class AgentRegistry:
    def __init__(self, workspace: str = ".", ai_router=None, db=None, file_manager=None):
        self.workspace = workspace
        self.ai = ai_router
        self.db = db
        self.fm = file_manager
        self._cache: dict[str, object] = {}

    def list_agents(self) -> list[dict]:
        return [{"id": k, "name": v} for k, v in AGENT_NAMES.items()]

    def get(self, agent_id: str):
        if agent_id in self._cache:
            return self._cache[agent_id]
        mod_path = AGENT_MODULES.get(agent_id)
        if not mod_path:
            raise KeyError(f"Unknown agent: {agent_id}")
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, "Agent")
        inst = cls(workspace=self.workspace, ai_router=self.ai, db=self.db, file_manager=self.fm)
        self._cache[agent_id] = inst
        return inst

    def run(self, agent_id: str, task: dict):
        return self.get(agent_id).run(task)

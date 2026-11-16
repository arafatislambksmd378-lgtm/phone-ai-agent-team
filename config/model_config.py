"""Model routing config — per-agent model selection, no hardcoded secrets."""
from __future__ import annotations
from dataclasses import dataclass, field

# Default model per agent role. Override via env MODEL_<AGENT>=... or settings.
DEFAULT_MODELS: dict[str, str] = {
    "chief_orchestrator": "gemini-2.0-flash",
    "requirement_analyst": "gemini-2.0-flash",
    "software_architect": "gemini-2.0-flash",
    "uiux_designer": "gemini-2.0-flash",
    "frontend_developer": "gemini-2.0-flash",
    "backend_developer": "gemini-2.0-flash",
    "database_engineer": "gemini-2.0-flash",
    "api_integrator": "gemini-2.0-flash",
    "security_engineer": "gemini-2.0-flash",
    "qa_tester": "gemini-2.0-flash",
    "debugger": "gemini-2.0-flash",
    "code_reviewer": "gemini-2.0-flash",
    "devops": "gemini-2.0-flash",
    "documentation": "gemini-2.0-flash",
    "release_manager": "gemini-2.0-flash",
    "workspace_executor": "gemini-2.0-flash",
}

# Capability requirements per agent: which providers can serve it.
AGENT_CAPABILITIES: dict[str, dict] = {
    "chief_orchestrator": {"needs_tools": False, "needs_json": True},
    "requirement_analyst": {"needs_tools": False, "needs_json": True},
    "software_architect": {"needs_tools": False, "needs_json": True},
    "uiux_designer": {"needs_tools": False, "needs_json": False},
    "frontend_developer": {"needs_tools": False, "needs_json": False},
    "backend_developer": {"needs_tools": False, "needs_json": False},
    "database_engineer": {"needs_tools": False, "needs_json": False},
    "api_integrator": {"needs_tools": False, "needs_json": False},
    "security_engineer": {"needs_tools": False, "needs_json": True},
    "qa_tester": {"needs_tools": True, "needs_json": False},
    "debugger": {"needs_tools": True, "needs_json": False},
    "code_reviewer": {"needs_tools": False, "needs_json": True},
    "devops": {"needs_tools": True, "needs_json": False},
    "documentation": {"needs_tools": False, "needs_json": False},
    "release_manager": {"needs_tools": False, "needs_json": True},
    "workspace_executor": {"needs_tools": True, "needs_json": False},
}

@dataclass
class ModelConfig:
    provider_order: list[str] = field(default_factory=lambda: ["gemini", "openai", "anthropic"])
    default_model: str = "gemini-2.0-flash"
    timeout_seconds: int = 60
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    agent_models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))

    def model_for(self, agent_id: str) -> str:
        return self.agent_models.get(agent_id, self.default_model)

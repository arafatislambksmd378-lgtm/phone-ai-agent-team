"""Model routing config — per-agent + per-provider model selection, no secrets."""
from __future__ import annotations
import os
from dataclasses import dataclass, field

DEFAULT_MODELS: dict[str, str] = {k: "gemini-2.0-flash" for k in (
    "chief_orchestrator", "requirement_analyst", "software_architect", "uiux_designer",
    "frontend_developer", "backend_developer", "database_engineer", "api_integrator",
    "security_engineer", "qa_tester", "debugger", "code_reviewer", "devops",
    "documentation", "release_manager", "workspace_executor",
)}


def _env_overrides() -> dict[str, str]:
    """Per-agent overrides via MODEL_<AGENT_ID> environment variables."""
    out = {}
    for agent_id in DEFAULT_MODELS:
        key = f"MODEL_{agent_id.upper()}"
        val = os.getenv(key)
        if val:
            out[agent_id] = val.strip()
    return out


@dataclass
class ModelConfig:
    provider_order: list[str] = field(default_factory=lambda: ["gemini", "openai", "anthropic"])
    default_model: str = "gemini-2.0-flash"
    # Capability flags are advisory only: they never hard-block the only configured provider.
    agent_models: dict[str, str] = field(default_factory=lambda: {**DEFAULT_MODELS, **_env_overrides()})
    provider_models: dict[str, str] = field(default_factory=lambda: {
        "gemini": os.getenv("AI_MODEL_GEMINI", os.getenv("AI_MODEL_DEFAULT", "gemini-2.0-flash")),
        "openai": os.getenv("AI_MODEL_OPENAI", "gpt-4o-mini"),
        "anthropic": os.getenv("AI_MODEL_ANTHROPIC", "claude-3-5-haiku-latest"),
    })

    def model_for(self, agent_id: str, provider: str | None = None) -> str:
        """Per-agent model when it matches the provider family, else provider default."""
        agent_model = self.agent_models.get(agent_id) or self.default_model
        if provider is None:
            return agent_model
        family = {"gemini": "gemini", "openai": ("gpt", "o1", "o3"), "anthropic": "claude"}[provider]
        if isinstance(family, str) and agent_model.startswith(family):
            return agent_model
        if isinstance(family, tuple) and agent_model.startswith(family):
            return agent_model
        return self.provider_models.get(provider, agent_model)

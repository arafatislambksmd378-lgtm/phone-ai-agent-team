"""AI Model Router — provider fallback, capability checks, usage tracking, mock mode."""
from __future__ import annotations
import time
from config.model_config import ModelConfig, AGENT_CAPABILITIES
from integrations.ai_providers.gemini import GeminiAdapter
from integrations.ai_providers.openai import OpenAIAdapter
from integrations.ai_providers.anthropic import AnthropicAdapter

# Rough USD per 1M tokens (for cost tracking where supported)
COST_PER_M = {"gemini": (0.15, 0.60), "openai": (0.15, 0.60), "anthropic": (0.80, 4.0)}

class ModelRouter:
    def __init__(self, settings, model_config: ModelConfig | None = None, db=None):
        self.settings = settings
        self.cfg = model_config or ModelConfig(provider_order=settings.ai_provider_order, default_model=settings.ai_model_default)
        self.db = db
        self.adapters = {
            "gemini": GeminiAdapter(settings.gemini_api_key, self.cfg.default_model),
            "openai": OpenAIAdapter(settings.openai_api_key, self.cfg.default_model, settings.openai_base_url),
            "anthropic": AnthropicAdapter(settings.anthropic_api_key, self.cfg.default_model),
        }

    def _capable(self, adapter, agent_id: str) -> bool:
        caps = AGENT_CAPABILITIES.get(agent_id, {})
        if caps.get("needs_tools") and not adapter.supports_tools:
            return False
        return True

    def complete(self, agent_id: str, system: str, user: str) -> dict:
        model = self.cfg.model_for(agent_id)
        tried = []
        for pname in self.cfg.provider_order:
            ad = self.adapters.get(pname)
            if ad is None or not ad.available():
                continue
            if not self._capable(ad, agent_id):
                tried.append(f"{pname}:capability")
                continue
            ad.model = model if pname == "gemini" else ad.model  # keep provider default unless gemini
            if pname == "gemini":
                ad.model = model
            r = ad.complete(system, user)
            if r.get("ok"):
                if self.db is not None:
                    pt, ct = r.get("prompt_tokens", 0), r.get("completion_tokens", 0)
                    rate = COST_PER_M.get(pname, (0, 0))
                    cost = (pt * rate[0] + ct * rate[1]) / 1_000_000
                    try:
                        self.db.record_usage(pname, r.get("model", model), agent_id, pt, ct, cost)
                    except Exception:
                        pass
                return r
            tried.append(f"{pname}:{r.get('error','')[:80]}")
        # Mock mode: deterministic fallback so system works offline / without keys
        if not any(a.available() for a in self.adapters.values()):
            return {"ok": True, "text": f"[MOCK {agent_id}] {user[:200]}", "provider": "mock", "model": model, "mock": True}
        return {"ok": False, "error": f"All providers failed: {'; '.join(tried)}", "tried": tried}

    def health(self) -> dict:
        return {k: {"available": v.available(), "model": v.model} for k, v in self.adapters.items()}

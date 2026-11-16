"""AI Model Router — per-provider models, explicit fallback, usage tracking, honest mock mode.

Modes reported on every result:
  "live"   — a real provider answered
  "mock"   — no provider configured; deterministic local output (NOT AI)
  "failed" — providers were configured but every attempt failed
"""
from __future__ import annotations
import logging

from config.model_config import ModelConfig
from integrations.ai_providers.anthropic import AnthropicAdapter
from integrations.ai_providers.gemini import GeminiAdapter
from integrations.ai_providers.openai import OpenAIAdapter

log = logging.getLogger("ai_router")

# Rough USD per 1M tokens (prompt, completion) — for usage estimates only.
COST_PER_M = {"gemini": (0.10, 0.40), "openai": (0.15, 0.60), "anthropic": (0.80, 4.00)}


class ModelRouter:
    def __init__(self, settings, model_config: ModelConfig | None = None, db=None):
        self.settings = settings
        timeout = getattr(settings, "ai_timeout_seconds", 60)
        self.cfg = model_config or ModelConfig(
            provider_order=list(settings.ai_provider_order),
            default_model=settings.ai_model_default,
            provider_models={
                "gemini": getattr(settings, "ai_model_gemini", settings.ai_model_default),
                "openai": getattr(settings, "ai_model_openai", "gpt-4o-mini"),
                "anthropic": getattr(settings, "ai_model_anthropic", "claude-3-5-haiku-latest"),
            },
        )
        self.allow_fallback = bool(getattr(settings, "allow_provider_fallback", True))
        self.db = db
        self.adapters = {
            "gemini": GeminiAdapter(settings.gemini_api_key,
                                    self.cfg.provider_models.get("gemini", ""), timeout),
            "openai": OpenAIAdapter(settings.openai_api_key,
                                    self.cfg.provider_models.get("openai", ""),
                                    settings.openai_base_url, timeout),
            "anthropic": AnthropicAdapter(settings.anthropic_api_key,
                                          self.cfg.provider_models.get("anthropic", ""), timeout),
        }

    # ---- state ----
    def configured_providers(self) -> list[str]:
        return [p for p in self.cfg.provider_order if p in self.adapters and self.adapters[p].available()]

    def mode(self) -> str:
        return "live" if self.configured_providers() else "mock"

    def health(self) -> dict:
        return {
            "mode": self.mode(),
            "fallback_enabled": self.allow_fallback,
            "providers": {name: {"status": ad.status(), "model": ad.model}
                          for name, ad in self.adapters.items()},
        }

    def _providers_to_try(self) -> list[str]:
        configured = self.configured_providers()
        if not configured:
            return []
        return configured if self.allow_fallback else configured[:1]

    # ---- main entry ----
    def complete(self, agent_id: str, system: str, user: str) -> dict:
        order = self._providers_to_try()
        if not order:
            return {"ok": True, "text": "", "provider": "mock", "model": "none",
                    "mode": "mock", "mock": True,
                    "error": "No provider configured — deterministic mock mode"}

        attempts: list[dict] = []
        for provider in order:
            adapter = self.adapters[provider]
            # Per-agent model only applies when it matches the provider family.
            adapter.model = self.cfg.model_for(agent_id, provider)
            result = adapter.complete(system, user)
            if result.get("ok"):
                self._record(provider, result, agent_id)
                result["mode"] = "live"
                result["mock"] = False
                result["attempts"] = attempts
                return result
            attempts.append({"provider": provider, "status": result.get("status"),
                             "error": result.get("error", "")[:200]})
            log.warning("provider %s failed (%s): %s", provider, result.get("status"),
                        result.get("error", "")[:200])
            if not self.allow_fallback:
                break
        return {"ok": False, "status": "ALL_PROVIDERS_FAILED", "mode": "failed", "mock": False,
                "error": "All configured providers failed",
                "providers_configured": order, "attempts": attempts}

    def _record(self, provider: str, result: dict, agent_id: str) -> None:
        if self.db is None:
            return
        pt = int(result.get("prompt_tokens", 0) or 0)
        ct = int(result.get("completion_tokens", 0) or 0)
        rate = COST_PER_M.get(provider, (0.0, 0.0))
        cost = (pt * rate[0] + ct * rate[1]) / 1_000_000
        try:
            self.db.record_usage(provider, result.get("model", ""), agent_id, pt, ct, cost)
        except Exception:
            log.debug("usage recording failed", exc_info=True)

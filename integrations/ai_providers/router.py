"""AI Model Router — per-provider models, extensible adapters, fallback, usage tracking.

Modes reported on every result:
  "live"   — a real provider answered
  "mock"   — no provider configured; deterministic local output (NOT AI)
  "failed" — providers were configured but every attempt failed
"""
from __future__ import annotations
import inspect
import logging

from config.model_config import ModelConfig
from integrations.ai_providers.anthropic import AnthropicAdapter
from integrations.ai_providers.base import ProviderAdapter
from integrations.ai_providers.gemini import GeminiAdapter
from integrations.ai_providers.openai import OpenAIAdapter

log = logging.getLogger("ai_router")

# Rough USD per 1M tokens (prompt, completion) — for usage estimates only.
COST_PER_M: dict[str, tuple[float, float]] = {
    "gemini": (0.10, 0.40),
    "openai": (0.15, 0.60),
    "codex": (2.50, 10.00),
    "anthropic": (0.80, 4.00),
}


class ModelRouter:
    def __init__(self, settings, model_config: ModelConfig | None = None, db=None):
        self.settings = settings
        timeout = getattr(settings, "ai_timeout_seconds", 60)
        max_tokens = getattr(settings, "ai_max_tokens", 4096)
        self.cfg = model_config or ModelConfig(
            provider_order=list(settings.ai_provider_order),
            default_model=settings.ai_model_default,
            provider_models={
                "gemini": getattr(settings, "ai_model_gemini", settings.ai_model_default),
                "openai": getattr(settings, "ai_model_openai", "gpt-4o-mini"),
                "codex": getattr(settings, "ai_model_codex", "gpt-4o"),
                "anthropic": getattr(settings, "ai_model_anthropic", "claude-3-5-haiku-latest"),
            },
        )
        self.allow_fallback = bool(getattr(settings, "allow_provider_fallback", True))
        self.db = db
        self._cost_rates = dict(COST_PER_M)
        self.adapters: dict[str, ProviderAdapter] = {
            "gemini": GeminiAdapter(settings.gemini_api_key,
                                    self.cfg.provider_models.get("gemini", ""),
                                    timeout, max_tokens=max_tokens),
            "openai": OpenAIAdapter(settings.openai_api_key,
                                    self.cfg.provider_models.get("openai", ""),
                                    settings.openai_base_url, timeout, max_tokens=max_tokens),
            "anthropic": AnthropicAdapter(settings.anthropic_api_key,
                                          self.cfg.provider_models.get("anthropic", ""),
                                          timeout, max_tokens=max_tokens),
        }
        codex_key = getattr(settings, "codex_api_key", "")
        if codex_key or "codex" in self.cfg.provider_order:
            self.register_provider(
                "codex",
                OpenAIAdapter(
                    codex_key,
                    self.cfg.provider_models.get("codex", "gpt-4o"),
                    getattr(settings, "codex_base_url", "https://api.openai.com/v1"),
                    timeout,
                    max_tokens=max_tokens,
                    provider_name="codex",
                ),
                cost_per_m=COST_PER_M["codex"],
            )

    # ---- extensibility ----
    def register_provider(self, name: str, adapter: ProviderAdapter,
                          cost_per_m: tuple[float, float] = (0.0, 0.0),
                          prepend: bool = False) -> None:
        """Register a custom or OpenAI-compatible provider adapter at runtime."""
        key = name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty")
        adapter.name = key
        self.adapters[key] = adapter
        self._cost_rates[key] = cost_per_m
        if adapter.model:
            self.cfg.provider_models.setdefault(key, adapter.model)
        if key not in self.cfg.provider_order:
            if prepend:
                self.cfg.provider_order.insert(0, key)
            else:
                self.cfg.provider_order.append(key)

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
    def complete(self, agent_id: str, system: str, user: str, *,
                 max_tokens: int | None = None, temperature: float = 0.2) -> dict:
        order = self._providers_to_try()
        if not order:
            return {"ok": True, "text": "", "provider": "mock", "model": "none",
                    "mode": "mock", "mock": True,
                    "error": "No provider configured — deterministic mock mode"}

        attempts: list[dict] = []
        for provider in order:
            adapter = self.adapters[provider]
            adapter.model = self.cfg.model_for(agent_id, provider)
            result = self._call_adapter(adapter, system, user, max_tokens=max_tokens,
                                        temperature=temperature)
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

    @staticmethod
    def _call_adapter(adapter: ProviderAdapter, system: str, user: str, *,
                      max_tokens: int | None = None, temperature: float = 0.2) -> dict:
        """Support both extended adapters and simple custom 2-arg complete(system, user) adapters."""
        try:
            sig = inspect.signature(adapter.complete)
            params = sig.parameters
            accepts_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            kwargs = {}
            if accepts_kw or "max_tokens" in params:
                kwargs["max_tokens"] = max_tokens
            if accepts_kw or "temperature" in params:
                kwargs["temperature"] = temperature
            return adapter.complete(system, user, **kwargs)
        except TypeError:
            return adapter.complete(system, user)

    def _record(self, provider: str, result: dict, agent_id: str) -> None:
        if self.db is None:
            return
        pt = int(result.get("prompt_tokens", 0) or 0)
        ct = int(result.get("completion_tokens", 0) or 0)
        rate = self._cost_rates.get(provider, (0.0, 0.0))
        cost = (pt * rate[0] + ct * rate[1]) / 1_000_000
        try:
            self.db.record_usage(provider, result.get("model", ""), agent_id, pt, ct, cost)
        except Exception:
            log.debug("usage recording failed", exc_info=True)

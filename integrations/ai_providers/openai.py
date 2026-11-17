"""OpenAI-compatible adapter (OpenAI, Codex gateways, OpenRouter, LM Studio, Azure-style…)."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    supports_tools = True
    supports_json = True

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "https://api.openai.com/v1", timeout: int = 60,
                 max_retries: int = 2, max_tokens: int = 4096, provider_name: str = "openai"):
        super().__init__(api_key, model, timeout, max_retries, max_tokens=max_tokens)
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.name = provider_name or "openai"

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> dict:
        if not self.available():
            return self._not_configured()
        if not self.model:
            return {"ok": False, "status": "BAD_REQUEST", "error": f"{self.name}: no model configured"}
        url = f"{self.base_url}/chat/completions"
        tokens = max(256, int(max_tokens or self.max_tokens))
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": float(temperature),
            "max_tokens": tokens,
        }
        r = self._post_json(url, payload, headers={"Authorization": f"Bearer {self.api_key}"})
        if not r.get("ok"):
            return r
        data = r["json"]
        try:
            choices = data.get("choices") or []
            if not choices:
                return {"ok": False, "status": "MALFORMED_RESPONSE",
                        "error": f"{self.name}: empty choices"}
            text = (choices[0].get("message") or {}).get("content") or ""
            if not text:
                return {"ok": False, "status": "MALFORMED_RESPONSE",
                        "error": f"{self.name}: empty content"}
            usage = data.get("usage") or {}
            return {"ok": True, "text": text, "provider": self.name,
                    "model": data.get("model", self.model),
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0)}
        except (KeyError, IndexError, TypeError) as e:
            return {"ok": False, "status": "MALFORMED_RESPONSE",
                    "error": f"{self.name} response shape unexpected: {e}"}

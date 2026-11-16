"""OpenAI-compatible adapter (OpenAI, Azure-style gateways, LM Studio, Ollama proxy…)."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    supports_tools = True
    supports_json = True

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "https://api.openai.com/v1", timeout: int = 60,
                 max_retries: int = 2):
        super().__init__(api_key, model, timeout, max_retries)
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def complete(self, system: str, user: str) -> dict:
        if not self.available():
            return self._not_configured()
        if not self.model:
            return {"ok": False, "status": "BAD_REQUEST", "error": "openai: no model configured"}
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        r = self._post_json(url, payload, headers={"Authorization": f"Bearer {self.api_key}"})
        if not r.get("ok"):
            return r
        data = r["json"]
        try:
            choices = data.get("choices") or []
            if not choices:
                return {"ok": False, "status": "MALFORMED_RESPONSE", "error": "OpenAI: empty choices"}
            text = (choices[0].get("message") or {}).get("content") or ""
            if not text:
                return {"ok": False, "status": "MALFORMED_RESPONSE", "error": "OpenAI: empty content"}
            usage = data.get("usage") or {}
            return {"ok": True, "text": text, "provider": "openai", "model": data.get("model", self.model),
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0)}
        except (KeyError, IndexError, TypeError) as e:
            return {"ok": False, "status": "MALFORMED_RESPONSE",
                    "error": f"OpenAI response shape unexpected: {e}"}

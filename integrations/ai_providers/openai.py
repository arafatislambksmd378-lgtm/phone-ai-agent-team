"""OpenAI-compatible adapter (OpenAI, Codex-compatible, LM Studio, etc.)."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter

class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    supports_tools = True
    supports_json = True

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "https://api.openai.com/v1", timeout: int = 60):
        super().__init__(api_key, model, timeout)
        self.base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str) -> dict:
        if not self.available():
            return {"ok": False, "error": "OPENAI_API_KEY missing"}
        model = self.model or "gpt-4o-mini"
        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": 0.3, "max_tokens": 2048}
        r = self._post_json(url, payload, headers={"Authorization": f"Bearer {self.api_key}"})
        if "error" in r and "choices" not in r:
            return {"ok": False, "error": str(r["error"])[:500]}
        try:
            text = r["choices"][0]["message"]["content"]
            usage = r.get("usage", {})
            return {"ok": True, "text": text, "provider": "openai", "model": model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0)}
        except Exception as e:
            return {"ok": False, "error": f"OpenAI parse: {e} raw={str(r)[:300]}"}

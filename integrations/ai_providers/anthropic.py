"""Anthropic Claude adapter."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter

class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    supports_tools = True
    supports_json = True

    def complete(self, system: str, user: str) -> dict:
        if not self.available():
            return {"ok": False, "error": "ANTHROPIC_API_KEY missing"}
        model = self.model or "claude-3-5-haiku-latest"
        url = "https://api.anthropic.com/v1/messages"
        payload = {"model": model, "max_tokens": 2048, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        r = self._post_json(url, payload, headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        if "error" in r and "content" not in r:
            return {"ok": False, "error": str(r["error"])[:500]}
        try:
            text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
            usage = r.get("usage", {})
            return {"ok": True, "text": text, "provider": "anthropic", "model": model,
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0)}
        except Exception as e:
            return {"ok": False, "error": f"Anthropic parse: {e} raw={str(r)[:300]}"}

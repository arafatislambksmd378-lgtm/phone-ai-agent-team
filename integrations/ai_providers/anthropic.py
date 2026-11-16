"""Anthropic Claude adapter — messages API, versioned header."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    supports_tools = True
    supports_json = True

    def complete(self, system: str, user: str) -> dict:
        if not self.available():
            return self._not_configured()
        if not self.model:
            return {"ok": False, "status": "BAD_REQUEST", "error": "anthropic: no model configured"}
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        r = self._post_json(url, payload, headers={
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        if not r.get("ok"):
            return r
        data = r["json"]
        try:
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            if not text:
                return {"ok": False, "status": "MALFORMED_RESPONSE",
                        "error": f"Anthropic: no text block (stop_reason={data.get('stop_reason')})"}
            usage = data.get("usage") or {}
            return {"ok": True, "text": text, "provider": "anthropic",
                    "model": data.get("model", self.model),
                    "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("output_tokens", 0) or 0)}
        except (KeyError, TypeError, AttributeError) as e:
            return {"ok": False, "status": "MALFORMED_RESPONSE",
                    "error": f"Anthropic response shape unexpected: {e}"}

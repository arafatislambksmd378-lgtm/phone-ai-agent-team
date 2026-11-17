"""Google Gemini adapter — correct generateContent request/response format."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter


class GeminiAdapter(ProviderAdapter):
    name = "gemini"
    supports_tools = False
    supports_json = True

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> dict:
        if not self.available():
            return self._not_configured()
        if not self.model:
            return {"ok": False, "status": "BAD_REQUEST", "error": "gemini: no model configured"}
        # Key travels in a header, not the URL (URLs end up in logs/proxies).
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        tokens = max(256, int(max_tokens or self.max_tokens))
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": float(temperature), "maxOutputTokens": tokens},
        }
        r = self._post_json(url, payload, headers={"x-goog-api-key": self.api_key})
        if not r.get("ok"):
            return r
        data = r["json"]
        try:
            candidates = data.get("candidates") or []
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                reason = candidates[0].get("finishReason", "NO_CONTENT") if candidates else "NO_CANDIDATES"
                return {"ok": False, "status": "MALFORMED_RESPONSE",
                        "error": f"Gemini returned no text (finishReason={reason})"}
            usage = data.get("usageMetadata", {}) or {}
            return {"ok": True, "text": text, "provider": "gemini", "model": self.model,
                    "prompt_tokens": int(usage.get("promptTokenCount", 0) or 0),
                    "completion_tokens": int(usage.get("candidatesTokenCount", 0) or 0)}
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            return {"ok": False, "status": "MALFORMED_RESPONSE",
                    "error": f"Gemini response shape unexpected: {e}"}

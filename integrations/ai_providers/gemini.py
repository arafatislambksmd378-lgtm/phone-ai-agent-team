"""Google Gemini adapter."""
from __future__ import annotations
from integrations.ai_providers.base import ProviderAdapter

class GeminiAdapter(ProviderAdapter):
    name = "gemini"
    supports_tools = False
    supports_json = True

    def complete(self, system: str, user: str) -> dict:
        if not self.available():
            return {"ok": False, "error": "GEMINI_API_KEY missing"}
        model = self.model or "gemini-2.0-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        payload = {"contents": [{"parts": [{"text": f"{system}\\n\\n{user}"}]}],
                   "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048}}
        r = self._post_json(url, payload)
        if not r.get("ok", True) and "error" in r and "candidates" not in r:
            return r
        try:
            text = r["candidates"][0]["content"]["parts"][0]["text"]
            usage = r.get("usageMetadata", {})
            return {"ok": True, "text": text, "provider": "gemini", "model": model,
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0)}
        except Exception as e:
            return {"ok": False, "error": f"Gemini parse: {e} raw={str(r)[:300]}"}

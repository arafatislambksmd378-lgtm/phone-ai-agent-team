"""Provider adapters — request shape, error classification, key hygiene.

No network is used: `_request` is monkeypatched to return canned responses.
"""
import json
import urllib.error

import pytest

from config.settings import Settings
from integrations.ai_providers.anthropic import AnthropicAdapter
from integrations.ai_providers.gemini import GeminiAdapter
from integrations.ai_providers.openai import OpenAIAdapter


def capture(adapter, response):
    """Record the outgoing request and return a canned transport result."""
    seen = {}

    def fake(url, payload=None, headers=None, method="POST"):
        seen.update(url=url, payload=payload, headers=headers)
        return response

    adapter._request = fake
    return seen


# ---------- Gemini ----------
def test_gemini_request_format(monkeypatch):
    a = GeminiAdapter("AIzaKEY", "gemini-2.0-flash")
    seen = capture(a, {"ok": True, "json": {
        "candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}})
    out = a.complete("sys", "user")
    assert out["ok"] and out["text"] == "hello"
    assert out["prompt_tokens"] == 10 and out["completion_tokens"] == 5
    assert "generateContent" in seen["url"]
    assert "key=" not in seen["url"], "API key must not be in the URL"
    assert seen["headers"]["x-goog-api-key"] == "AIzaKEY"
    assert seen["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["payload"]["contents"][0]["parts"][0]["text"] == "user"


def test_gemini_malformed_response():
    a = GeminiAdapter("k", "gemini-2.0-flash")
    capture(a, {"ok": True, "json": {"unexpected": True}})
    out = a.complete("s", "u")
    assert out["ok"] is False and out["status"] == "MALFORMED_RESPONSE"


def test_gemini_blocked_content():
    a = GeminiAdapter("k", "gemini-2.0-flash")
    capture(a, {"ok": True, "json": {"candidates": [{"finishReason": "SAFETY", "content": {}}]}})
    out = a.complete("s", "u")
    assert out["ok"] is False and "SAFETY" in out["error"]


# ---------- OpenAI ----------
def test_openai_request_format():
    a = OpenAIAdapter("sk-test", "gpt-4o-mini", "https://api.example.com/v1")
    seen = capture(a, {"ok": True, "json": {
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4}, "model": "gpt-4o-mini"}})
    out = a.complete("sys", "user")
    assert out["ok"] and out["text"] == "hi"
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert [m["role"] for m in seen["payload"]["messages"]] == ["system", "user"]


def test_openai_empty_choices():
    a = OpenAIAdapter("k", "gpt-4o-mini")
    capture(a, {"ok": True, "json": {"choices": []}})
    out = a.complete("s", "u")
    assert out["ok"] is False and out["status"] == "MALFORMED_RESPONSE"


def test_openai_custom_base_url():
    a = OpenAIAdapter("k", "local-model", "http://127.0.0.1:1234/v1/")
    seen = capture(a, {"ok": True, "json": {"choices": [{"message": {"content": "x"}}]}})
    a.complete("s", "u")
    assert seen["url"] == "http://127.0.0.1:1234/v1/chat/completions"


# ---------- Anthropic ----------
def test_anthropic_request_format():
    a = AnthropicAdapter("sk-ant-x", "claude-3-5-haiku-latest")
    seen = capture(a, {"ok": True, "json": {
        "content": [{"type": "text", "text": "claude says hi"}],
        "usage": {"input_tokens": 7, "output_tokens": 9}, "model": "claude-3-5-haiku-latest"}})
    out = a.complete("sys", "user")
    assert out["ok"] and out["text"] == "claude says hi"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-x"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["payload"]["system"] == "sys"
    assert seen["payload"]["messages"][0]["role"] == "user"


def test_anthropic_no_text_block():
    a = AnthropicAdapter("k", "claude-3-5-haiku-latest")
    capture(a, {"ok": True, "json": {"content": [], "stop_reason": "max_tokens"}})
    out = a.complete("s", "u")
    assert out["ok"] is False and out["status"] == "MALFORMED_RESPONSE"


# ---------- error classification via real HTTP error path ----------
def http_error(code):
    import io

    def fake(url, payload=None, headers=None, method="POST"):
        err = urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b'{"error":"api key sk-leaked123456789"}'))
        try:
            body = err.read(400).decode()
        except Exception:
            body = ""
        return {"ok": False, "status": {401: "AUTH_ERROR", 403: "AUTH_ERROR", 429: "RATE_LIMITED",
                                        500: "SERVER_ERROR", 400: "BAD_REQUEST"}.get(code, "UNKNOWN"),
                "retryable": code in (429, 500), "http_status": code,
                "error": f"HTTP {code}: {body}"}

    return fake


@pytest.mark.parametrize("code,status,retryable", [
    (401, "AUTH_ERROR", False),
    (403, "AUTH_ERROR", False),
    (429, "RATE_LIMITED", True),
    (500, "SERVER_ERROR", True),
    (400, "BAD_REQUEST", False),
])
def test_error_classification(code, status, retryable):
    a = OpenAIAdapter("sk-test", "m")
    a._request = http_error(code)
    a.max_retries = 0
    out = a.complete("s", "u")
    assert out["status"] == status
    assert out.get("retryable") is retryable


def test_error_body_never_contains_live_key():
    a = GPT = OpenAIAdapter("sk-live-SECRETKEY123456789", "m")
    a._request = http_error(401)
    a.max_retries = 0
    out = a.complete("s", "u")
    assert "sk-leaked123456789" in out["error"] or "REDACTED" in out["error"]
    assert "SECRETKEY" not in json.dumps(out)


def test_timeout_is_classified():
    def fake(url, payload=None, headers=None, method="POST"):
        return {"ok": False, "status": "TIMEOUT", "retryable": True, "error": "Request timed out"}

    a = GeminiAdapter("k", "gemini-2.0-flash")
    a._request = fake
    a.max_retries = 0
    assert a.complete("s", "u")["status"] == "TIMEOUT"


def test_not_configured_adapters():
    for adapter in (GeminiAdapter("", "m"), OpenAIAdapter("", "m"), AnthropicAdapter("", "m")):
        out = adapter.complete("s", "u")
        assert out["ok"] is False and out["status"] == "NOT_CONFIGURED"


def test_missing_model_is_reported():
    out = GeminiAdapter("key", "").complete("s", "u")
    assert out["ok"] is False and out["status"] == "BAD_REQUEST"

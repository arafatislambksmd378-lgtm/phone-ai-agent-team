"""Provider extensibility tests + opt-in LIVE external integration tests (RUN_LIVE_TESTS=1)."""
from __future__ import annotations

import os
import pytest

from config.settings import Settings
from integrations.ai_providers.base import ProviderAdapter
from integrations.ai_providers.router import ModelRouter


def test_codex_and_custom_provider_adapter_registration():
    s = Settings(
        codex_api_key="sk-codex-test",
        codex_base_url="https://api.openai.com/v1",
        ai_provider_order=["codex"],
    )
    router = ModelRouter(s)
    assert "codex" in router.adapters
    assert router.mode() == "live"

    class CustomLocalAdapter(ProviderAdapter):
        name = "custom_adapter"

        def complete(self, system: str, user: str, **kwargs) -> dict:
            return {
                "ok": True,
                "text": f"custom:{user[:20]}",
                "provider": self.name,
                "model": self.model,
                "prompt_tokens": 5,
                "completion_tokens": 5,
            }

    router.register_provider("custom_adapter", CustomLocalAdapter(api_key="k", model="m1"), prepend=True)
    resp = router.complete("backend_developer", "sys", "hello world")
    assert resp["ok"]
    assert resp["provider"] == "custom_adapter"
    assert resp["text"].startswith("custom:hello world")


# ==============================================================================
# Opt-in Live External Verification Suite
# Run on target host / Termux with: RUN_LIVE_TESTS=1 pytest tests/test_live_integrations.py -v
# ==============================================================================

LIVE_ENABLED = os.getenv("RUN_LIVE_TESTS", "").strip() in {"1", "true", "yes"}


@pytest.mark.skipif(not (LIVE_ENABLED and os.getenv("GEMINI_API_KEY")), reason="Requires RUN_LIVE_TESTS=1 and GEMINI_API_KEY")
def test_live_gemini_completion():
    s = Settings(ai_provider_order=["gemini"])
    r = ModelRouter(s)
    resp = r.complete("chief_orchestrator", "Be concise.", "Reply with the single word READY.", max_tokens=32)
    assert resp["ok"], resp.get("error")
    assert "READY" in resp["text"].upper()


@pytest.mark.skipif(not (LIVE_ENABLED and os.getenv("OPENAI_API_KEY")), reason="Requires RUN_LIVE_TESTS=1 and OPENAI_API_KEY")
def test_live_openai_completion():
    s = Settings(ai_provider_order=["openai"])
    r = ModelRouter(s)
    resp = r.complete("chief_orchestrator", "Be concise.", "Reply with the single word READY.", max_tokens=32)
    assert resp["ok"], resp.get("error")
    assert "READY" in resp["text"].upper()


@pytest.mark.skipif(not (LIVE_ENABLED and os.getenv("ANTHROPIC_API_KEY")), reason="Requires RUN_LIVE_TESTS=1 and ANTHROPIC_API_KEY")
def test_live_anthropic_completion():
    s = Settings(ai_provider_order=["anthropic"])
    r = ModelRouter(s)
    resp = r.complete("chief_orchestrator", "Be concise.", "Reply with the single word READY.", max_tokens=32)
    assert resp["ok"], resp.get("error")
    assert "READY" in resp["text"].upper()


@pytest.mark.skipif(not (LIVE_ENABLED and os.getenv("TELEGRAM_BOT_TOKEN")), reason="Requires RUN_LIVE_TESTS=1 and TELEGRAM_BOT_TOKEN")
def test_live_telegram_bot_get_me():
    from integrations.telegram.bot import TelegramBot
    bot = TelegramBot(os.environ["TELEGRAM_BOT_TOKEN"], int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "1") or "1"), lambda *_: None)
    me = bot._api("getMe")
    assert me.get("ok") is True

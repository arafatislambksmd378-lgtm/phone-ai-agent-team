from config.settings import Settings
from integrations.ai_providers.router import ModelRouter

def test_mock_mode_without_keys():
    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1, gemini_api_key="", openai_api_key="", anthropic_api_key="")
    r = ModelRouter(s)
    out = r.complete("backend_developer", "sys", "hello")
    assert out["ok"] and out.get("mock") is True

def test_health():
    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1)
    h = ModelRouter(s).health()
    assert "gemini" in h and "openai" in h and "anthropic" in h

from config.settings import Settings
from integrations.ai_providers.router import ModelRouter


def _settings(**kw):
    base = dict(telegram_bot_token="x", telegram_allowed_user_id=1,
                gemini_api_key="", openai_api_key="", anthropic_api_key="")
    base.update(kw)
    return Settings(**base)


def test_mock_mode_without_keys():
    r = ModelRouter(_settings())
    assert r.mode() == "mock"
    out = r.complete("backend_developer", "sys", "hello")
    assert out["ok"] and out["mock"] is True and out["mode"] == "mock"


def test_health_shape():
    h = ModelRouter(_settings()).health()
    assert h["mode"] == "mock"
    assert set(h["providers"]) == {"gemini", "openai", "anthropic"}
    assert h["providers"]["gemini"]["status"] == "NOT_CONFIGURED"
    assert h["providers"]["openai"]["model"] == "gpt-4o-mini"


def test_per_provider_models_are_not_gemini():
    r = ModelRouter(_settings(openai_api_key="x", anthropic_api_key="y"))
    assert r.adapters["openai"].model.startswith("gpt")
    assert r.adapters["anthropic"].model.startswith("claude")
    assert r.adapters["gemini"].model.startswith("gemini")


def test_configured_but_unreachable_reports_failed_not_mock():
    """A configured provider that cannot be reached must be a failure, never 'mock'."""
    r = ModelRouter(_settings(gemini_api_key="invalid-key-for-test"), )
    r.adapters["gemini"].max_retries = 0
    r.adapters["gemini"].timeout = 1
    out = r.complete("backend_developer", "sys", "hi")
    assert out["ok"] is False
    assert out["mode"] == "failed"
    assert out["mock"] is False


def test_fallback_disabled_only_tries_first_provider():
    r = ModelRouter(_settings(gemini_api_key="a", openai_api_key="b", allow_provider_fallback=False))
    assert r._providers_to_try() == ["gemini"]


def test_key_never_in_repr():
    r = ModelRouter(_settings(gemini_api_key="AIzaSECRETSECRETSECRETSECRET123456"))
    text = repr(r.adapters["gemini"])
    assert "SECRETSECRET" not in text


def test_usage_records_project_id_and_successful_provider_is_router_authoritative(tmp_path, monkeypatch):
    """Token/cost telemetry must be attributable to a project, and fallback provider names accurate."""
    from memory.database import Database

    db = Database(tmp_path / "usage.db")
    r = ModelRouter(_settings(gemini_api_key="key-1", openai_api_key="key-2"), db=db)

    def fake_complete(self, system, user, **kwargs):
        return {"ok": True, "text": "READY", "provider": "family-name",
                "prompt_tokens": 10, "completion_tokens": 2}

    monkeypatch.setattr(type(r.adapters["gemini"]), "complete", fake_complete)
    out = r.complete("qa_tester", "sys", "hi", project_id="proj-42")
    assert out["ok"] and out["provider"] == "gemini" and out["mode"] == "live"
    rows = db.query("SELECT provider,project_id,prompt_tokens,completion_tokens,cost_usd FROM model_usage")
    assert len(rows) == 1 and rows[0]["project_id"] == "proj-42"
    assert rows[0]["prompt_tokens"] == 10 and rows[0]["cost_usd"] > 0
    db.close()

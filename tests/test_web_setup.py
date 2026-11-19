"""Tests for the Web API Key & Live Verification Portal (Cloud LLMs + Telegram)."""
from __future__ import annotations

import json
import os
import stat
import threading
import urllib.request
from pathlib import Path

from config.settings import Settings
from integrations.web.env_store import masked_env_status, update_env_file
from integrations.web.setup_server import SetupPortalController, create_setup_http_server
from integrations.web.ui_page import SETUP_HTML


def test_env_store_writes_0600_preserves_existing_secrets_and_never_leaks_raw_keys(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"):
        monkeypatch.delenv(k, raising=False)

    env_file = tmp_path / ".env"
    changed = update_env_file(
        {
            "GEMINI_API_KEY": "AIzaSyTestSecretKeyValue12345\nINJECTED=1",
            "AI_MODEL_GEMINI": "gemini-2.0-flash",
            "TELEGRAM_ALLOWED_USER_ID": "9876543",
        },
        env_file,
    )
    assert "GEMINI_API_KEY" in changed
    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600

    # Newline injection stripped cleanly
    raw_content = env_file.read_text(encoding="utf-8")
    assert "\nINJECTED=1" not in raw_content

    # Saving Telegram token with blank GEMINI_API_KEY preserves existing GEMINI_API_KEY!
    update_env_file({"GEMINI_API_KEY": "", "TELEGRAM_BOT_TOKEN": "123456:ABCDEF_SECRET_TOKEN"}, env_file)

    masked = masked_env_status(env_file)
    assert masked["GEMINI_API_KEY"]["configured"] is True
    assert "AIzaSyTestSecretKeyValue12345" not in json.dumps(masked)
    assert masked["TELEGRAM_BOT_TOKEN"]["configured"] is True
    assert "ABCDEF_SECRET_TOKEN" not in json.dumps(masked)

    # Explicit clear removes only requested key
    update_env_file({}, env_file, clear_keys=["GEMINI_API_KEY"])
    masked_after = masked_env_status(env_file)
    assert masked_after["GEMINI_API_KEY"]["configured"] is False
    assert masked_after["TELEGRAM_BOT_TOKEN"]["configured"] is True


def test_setup_portal_http_endpoints_and_preview_host_compatibility(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    # Browser-facing HTML must never hardcode localhost/127.0.0.1 for API calls
    assert "http://localhost" not in SETUP_HTML
    assert "http://127.0.0.1" not in SETUP_HTML

    env_file = tmp_path / ".env"

    def make_settings() -> Settings:
        s = SetupPortalController(env_path=env_file)._build_settings_from_env()
        s.workspace_root = tmp_path / "ws"
        s.db_path = tmp_path / "portal.db"
        return s

    ctrl = SetupPortalController(env_path=env_file, settings_factory=make_settings)
    srv = create_setup_http_server(host="127.0.0.1", port=0, controller=ctrl)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    try:
        # 1. GET / works with proxied preview Host header and has no iframe-blocking header
        req_html = urllib.request.Request(f"{base}/", headers={"Host": f"{port}-sandbox.e2b.app"})
        with urllib.request.urlopen(req_html, timeout=5) as r:
            html = r.read().decode("utf-8")
            assert r.status == 200
            assert r.headers.get("X-Frame-Options") is None
            assert "GEMINI_API_KEY" in html
            assert "TELEGRAM_BOT_TOKEN" in html

        # 2. POST /api/save rejects non-numeric TELEGRAM_ALLOWED_USER_ID
        bad_req = urllib.request.Request(
            f"{base}/api/save",
            data=json.dumps({"updates": {"TELEGRAM_ALLOWED_USER_ID": "not_a_number"}}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(bad_req, timeout=5) as r:
            bad_res = json.loads(r.read().decode())
            assert bad_res["ok"] is False

        # 3. POST /api/save stores LLM & Telegram keys and GET /api/status returns masked status
        save_req = urllib.request.Request(
            f"{base}/api/save",
            data=json.dumps({
                "updates": {
                    "OPENAI_API_KEY": "sk-test-super-secret-value-9999",
                    "TELEGRAM_BOT_TOKEN": "999888:AAH_SUPER_SECRET_TG",
                    "TELEGRAM_ALLOWED_USER_ID": "424242",
                }
            }).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(save_req, timeout=5) as r:
            save_res = json.loads(r.read().decode())
            assert save_res["ok"] is True

        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
            st = json.loads(r.read().decode())
            assert st["ai_mode"] == "live"
            assert st["env"]["OPENAI_API_KEY"]["configured"] is True
            assert "sk-test-super-secret-value-9999" not in json.dumps(st)
            assert "AAH_SUPER_SECRET_TG" not in json.dumps(st)

        # 4. Verify Telegram test modes (`get_me`, `inline_approval`, `send_document`) via stubbed transport
        monkeypatch.setattr(
            "integrations.telegram.bot.TelegramBot._api",
            lambda self, method, payload=None, timeout=30: (
                {"ok": True, "result": {"username": "phone_agent_bot", "first_name": "PhoneAgent", "id": 101}}
                if method == "getMe"
                else {"ok": True, "result": {"message_id": 55}}
            ),
        )
        monkeypatch.setattr(
            "integrations.telegram.bot.TelegramBot.send_document",
            lambda self, chat_id, file_path, caption="": {"ok": True, "file": Path(file_path).name},
        )

        for mode in ("get_me", "inline_approval", "send_document"):
            tg_req = urllib.request.Request(
                f"{base}/api/test-telegram",
                data=json.dumps({"mode": mode}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(tg_req, timeout=10) as r:
                tg_res = json.loads(r.read().decode())
                assert tg_res["ok"] is True, tg_res
    finally:
        srv.shutdown()
        ctrl.shutdown()
        srv.server_close()

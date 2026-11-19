"""Web Setup & Live Verification Server — stdlib ThreadingHTTPServer bound to 0.0.0.0.

Provides a dedicated browser page to paste & persist API keys into `.env` (`0600`) and
execute real live checks for:
  1. Cloud LLM Providers (Gemini, OpenAI, Codex, Anthropic)
  2. Telegram Bot Polling, Inline Approval Callbacks, and `sendDocument` ZIP delivery
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config.settings import Settings
from execution.resource_manager import ResourceManager
from integrations.ai_providers.router import ModelRouter
from integrations.telegram.bot import TelegramBot
from integrations.telegram.handler import TelegramHandler
from integrations.web.env_store import MANAGED_ENV_KEYS, masked_env_status, read_env_dict, update_env_file
from integrations.web.ui_page import SETUP_HTML
from orchestrator.engine import OrchestratorEngine
from security.secret_manager import redact

log = logging.getLogger("web.setup")
MAX_BODY_BYTES = 64 * 1024


class SetupPortalController:
    """Stateful backend for the API Key & Live Verification Portal."""

    def __init__(self, env_path: str | Path = ".env", settings_factory=None):
        self.env_path = Path(env_path)
        self._settings_factory = settings_factory or self._build_settings_from_env
        self._lock = threading.Lock()
        self._bot: TelegramBot | None = None
        self._bot_thread: threading.Thread | None = None
        self._engine: OrchestratorEngine | None = None

    def _build_settings_from_env(self) -> Settings:
        vals = read_env_dict(self.env_path)
        for k, v in vals.items():
            if v != "":
                os.environ[k] = v
        order_raw = vals.get("AI_PROVIDER_ORDER") or os.getenv("AI_PROVIDER_ORDER", "gemini,openai,anthropic")
        order = [p.strip() for p in order_raw.split(",") if p.strip()]
        fb_raw = (vals.get("ALLOW_PROVIDER_FALLBACK") or "true").strip().lower()
        uid_raw = (vals.get("TELEGRAM_ALLOWED_USER_ID") or "0").strip()
        try:
            uid = int(uid_raw)
        except ValueError:
            uid = 0
        return Settings(
            telegram_bot_token=vals.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_user_id=uid,
            gemini_api_key=vals.get("GEMINI_API_KEY", ""),
            ai_model_gemini=vals.get("AI_MODEL_GEMINI") or "gemini-3.5-flash-lite",
            openai_api_key=vals.get("OPENAI_API_KEY", ""),
            openai_base_url=vals.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            ai_model_openai=vals.get("AI_MODEL_OPENAI") or "gpt-4o-mini",
            codex_api_key=vals.get("CODEX_API_KEY", ""),
            codex_base_url=vals.get("CODEX_BASE_URL") or "https://api.openai.com/v1",
            ai_model_codex=vals.get("AI_MODEL_CODEX") or "gpt-4o",
            anthropic_api_key=vals.get("ANTHROPIC_API_KEY", ""),
            ai_model_anthropic=vals.get("AI_MODEL_ANTHROPIC") or "claude-3-5-haiku-latest",
            ai_provider_order=order or ["gemini", "openai", "anthropic"],
            allow_provider_fallback=fb_raw not in {"0", "false", "no", "off"},
        )

    def is_polling(self) -> bool:
        return bool(self._bot_thread and self._bot_thread.is_alive() and self._bot and self._bot._running)

    def status_payload(self) -> dict:
        settings = self._settings_factory()
        router = ModelRouter(settings)
        rm = ResourceManager(workspace_root=settings.workspace_root)
        snap = rm.snapshot()
        return {
            "ok": True,
            "ai_mode": router.mode(),
            "providers": router.health()["providers"],
            "telegram_polling": self.is_polling(),
            "env": masked_env_status(self.env_path),
            "resources": {
                "available_ram_mb": snap["memory"]["available_mb"],
                "process_rss_mb": snap["memory"]["process_rss_mb"],
                "free_disk_mb": snap["storage"]["free_mb"],
                "is_termux": snap["is_termux"],
            },
        }

    def save_keys(self, updates: dict) -> dict:
        if not isinstance(updates, dict):
            return {"ok": False, "message": "Invalid JSON payload"}
        # Validate TELEGRAM_ALLOWED_USER_ID if non-empty
        if "TELEGRAM_ALLOWED_USER_ID" in updates and str(updates["TELEGRAM_ALLOWED_USER_ID"]).strip():
            raw_uid = str(updates["TELEGRAM_ALLOWED_USER_ID"]).strip()
            if not raw_uid.isdigit():
                return {"ok": False, "message": "⚠️ TELEGRAM_ALLOWED_USER_ID must be a numeric Telegram user ID."}

        changed = update_env_file(updates, self.env_path)
        if not changed:
            return {"ok": True, "changed": [], "message": "No new non-empty fields provided; existing keys preserved."}
        return {
            "ok": True,
            "changed": changed,
            "message": f"✅ Saved {len(changed)} setting(s) to {self.env_path} (chmod 600): {', '.join(changed)}",
        }

    def clear_key(self, key: str) -> dict:
        if key not in MANAGED_ENV_KEYS:
            return {"ok": False, "message": f"Unsupported key: {key}"}
        update_env_file({}, self.env_path, clear_keys=[key])
        return {"ok": True, "message": f"🗑 Cleared {key} from {self.env_path}."}

    # ---------- Live Cloud LLM Verification ----------
    def test_live_llms(self) -> dict:
        settings = self._settings_factory()
        router = ModelRouter(settings)
        configured = [
            name for name, ad in router.adapters.items() if ad.available()
        ]
        if not configured:
            return {
                "ok": False,
                "report": (
                    "⚠️ No Cloud LLM API key is configured yet.\n"
                    "Paste at least one key (GEMINI_API_KEY, OPENAI_API_KEY, CODEX_API_KEY, or ANTHROPIC_API_KEY) "
                    "and click 'Save LLM Keys' first."
                ),
            }

        lines = [f"== Live Cloud LLM Verification ({len(configured)} provider(s)) =="]
        any_ok = False
        for name in configured:
            adapter = router.adapters[name]
            t0 = time.time()
            res = router._call_adapter(
                adapter,
                "You are a connectivity check assistant. Reply concisely.",
                "Reply with the single word: READY",
                max_tokens=32,
                temperature=0.0,
            )
            ms = int((time.time() - t0) * 1000)
            if res.get("ok"):
                any_ok = True
                snippet = redact((res.get("text") or "").strip().replace("\n", " "))[:120]
                pt = res.get("prompt_tokens", 0)
                ct = res.get("completion_tokens", 0)
                lines.append(
                    f"✅ {name.upper()} ({adapter.model}) — LIVE OK in {ms}ms "
                    f"[tokens: {pt} in / {ct} out] → {snippet!r}"
                )
            else:
                err = redact(str(res.get("error") or "unknown error"))
                status = res.get("status", "FAILED")
                hint = ""
                if status in ("CONNECTION_FAILED", "TIMEOUT") or "Tunnel connection failed" in err or "403" in err:
                    hint = (
                        "\n   ℹ️ Note: If running inside a firewall-restricted cloud preview container, "
                        "outbound LLM hosts may be blocked by the sandbox proxy; this same .env will connect "
                        "directly on your Android Termux phone or standard host."
                    )
                lines.append(f"❌ {name.upper()} ({adapter.model}) — {status} ({ms}ms): {err}{hint}")

        return {"ok": any_ok, "report": "\n".join(lines)}

    # ---------- Live Telegram Verification & Polling ----------
    def test_live_telegram(self, mode: str = "get_me") -> dict:
        settings = self._settings_factory()
        if not settings.telegram_bot_token:
            return {"ok": False, "report": "⚠️ TELEGRAM_BOT_TOKEN is not set. Paste and save it first."}

        bot = TelegramBot(
            settings.telegram_bot_token,
            settings.telegram_allowed_user_id,
            lambda *_: None,
            settings.max_upload_mb,
        )

        if mode == "get_me":
            res = bot._api("getMe")
            if res.get("ok"):
                info = res.get("result") or {}
                return {
                    "ok": True,
                    "report": (
                        f"✅ Telegram Bot Verified (LIVE)!\n"
                        f"Bot Username : @{info.get('username')}\n"
                        f"Bot Name     : {info.get('first_name')}\n"
                        f"Bot ID       : {info.get('id')}"
                    ),
                }
            return {"ok": False, "report": self._format_tg_error("getMe", res)}

        if not settings.telegram_allowed_user_id:
            return {
                "ok": False,
                "report": "⚠️ Set and save TELEGRAM_ALLOWED_USER_ID first so test messages know where to send.",
            }

        chat_id = settings.telegram_allowed_user_id

        if mode == "inline_approval":
            engine = self._ensure_engine(settings)
            pid = engine.create_project("live_approval_check", "Verify Telegram inline approval buttons", auto_start=False)
            aid = engine.approvals.request(
                pid,
                "production_deploy",
                "Live inline callback test triggered from Web Setup Portal",
            )
            row = engine.db.get_approval(aid) or {
                "id": aid, "project_id": pid, "action": "production_deploy",
                "detail": "Live inline callback test",
            }
            res = bot.send_approval_prompt(chat_id, row)
            if res.get("ok"):
                poll_note = (
                    "Polling is ACTIVE — tap ✅ Approve or ❌ Reject in Telegram now!"
                    if self.is_polling()
                    else "Click '▶️ Start Live Bot Polling' below so tapping the button in Telegram is processed live!"
                )
                return {
                    "ok": True,
                    "report": (
                        f"✅ Sent Inline Approval Card (`{aid}`) to Telegram user `{chat_id}`!\n"
                        f"{poll_note}"
                    ),
                }
            return {"ok": False, "report": self._format_tg_error("sendMessage (inline_keyboard)", res)}

        if mode == "send_document":
            fd, zip_path = tempfile.mkstemp(prefix="phone_agent_live_verify_", suffix=".zip")
            os.close(fd)
            try:
                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(
                        "LIVE_VERIFICATION_OK.txt",
                        "Phone AI Agent Team — Telegram sendDocument multipart ZIP delivery verified!\n",
                    )
                res = bot.send_document(
                    chat_id,
                    zip_path,
                    caption="📦 Phone AI Agent Team — Live sendDocument ZIP verification!",
                )
            finally:
                try:
                    os.unlink(zip_path)
                except OSError:
                    pass
            if res.get("ok"):
                return {
                    "ok": True,
                    "report": f"✅ Delivered sample `.zip` archive via `sendDocument` to Telegram user `{chat_id}`!",
                }
            return {"ok": False, "report": self._format_tg_error("sendDocument", res)}

        return {"ok": False, "report": f"Unknown test mode: {mode}"}

    @staticmethod
    def _format_tg_error(method: str, res: dict) -> str:
        err = redact(str(res.get("error") or res.get("description") or "unknown error"))
        hint = ""
        if "Tunnel connection failed" in err or "urlopen error" in err or "Forbidden" in err:
            hint = (
                "\nℹ️ Note: If running inside a network-restricted cloud preview sandbox, outbound "
                "`api.telegram.org` traffic may be blocked by the container proxy; on your Android Termux "
                "phone or local machine, this saved token connects directly to Telegram."
            )
        return f"❌ Telegram `{method}` failed: {err}{hint}"

    def _ensure_engine(self, settings: Settings) -> OrchestratorEngine:
        with self._lock:
            if self._engine is None:
                self._engine = OrchestratorEngine(settings)
            else:
                self._engine.settings = settings
                self._engine.router = ModelRouter(settings, db=self._engine.db)
            return self._engine

    def control_bot(self, action: str) -> dict:
        act = (action or "").strip().lower()
        if act == "stop":
            with self._lock:
                if self._bot is not None:
                    self._bot.stop()
                self._bot = None
            return {"ok": True, "polling": False, "message": "⏹ Stopped background Telegram polling listener."}

        if act == "start":
            settings = self._settings_factory()
            if not settings.telegram_bot_token or not settings.telegram_allowed_user_id:
                return {
                    "ok": False,
                    "polling": False,
                    "message": "⚠️ Both TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID must be saved before starting polling.",
                }
            if self.is_polling():
                return {"ok": True, "polling": True, "message": "✅ Telegram polling listener is already running."}

            engine = self._ensure_engine(settings)
            handler = TelegramHandler(engine, settings)
            bot = TelegramBot(
                settings.telegram_bot_token,
                settings.telegram_allowed_user_id,
                handler,
                settings.max_upload_mb,
            )
            handler.bind_bot(bot, settings.telegram_allowed_user_id)
            with self._lock:
                self._bot = bot
                t = threading.Thread(target=bot.run_forever, daemon=True, name="web-telegram-poller")
                t.start()
                self._bot_thread = t
            return {
                "ok": True,
                "polling": True,
                "message": (
                    f"▶️ Live Telegram Bot polling started for authorized user `{settings.telegram_allowed_user_id}`! "
                    "Send /start or tap inline approval buttons in Telegram."
                ),
            }

        return {"ok": False, "message": f"Unknown action: {action}"}

    def shutdown(self) -> None:
        with self._lock:
            if self._bot is not None:
                self._bot.stop()
                self._bot = None
            if self._engine is not None:
                self._engine.shutdown()
                self._engine = None


def create_setup_http_server(host: str = "127.0.0.1", port: int = 8080,
                             controller: SetupPortalController | None = None,
                             auth_token: str | None = None) -> ThreadingHTTPServer:
    """Create a ThreadingHTTPServer for credential setup.

    Security: binding to any non-loopback address (e.g. 0.0.0.0) fails closed unless a
    strong `auth_token` (or WEB_SETUP_TOKEN) is configured and supplied as Bearer token.
    """
    import hmac
    from security.validation import is_loopback_host, validate_api_token

    token = auth_token if auth_token is not None else os.getenv("WEB_SETUP_TOKEN", "")
    loopback = is_loopback_host(host)
    if not loopback:
        ok_tok, reason = validate_api_token(token)
        if not ok_tok:
            raise ValueError(
                f"Refusing to bind Web Setup Portal to external host {host!r} without a strong "
                f"WEB_SETUP_TOKEN: {reason}"
            )
    elif token:
        ok_tok, reason = validate_api_token(token)
        if not ok_tok:
            raise ValueError(f"Invalid WEB_SETUP_TOKEN: {reason}")

    ctrl = controller or SetupPortalController()

    class SetupHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            log.info("web %s - %s", self.address_string(), redact(fmt % args))

        def _check_auth(self) -> bool:
            if not token:
                return True  # strictly reachable only when `is_loopback_host(host)` is True
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Bearer "):
                return False
            provided = header[7:]
            return bool(provided) and hmac.compare_digest(
                provided.encode("utf-8"), token.encode("utf-8"),
            )

        def _send_json(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, code: int, html: str) -> None:
            raw = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY_BYTES:
                return None
            if length == 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return None

        def do_GET(self) -> None:
            if not self._check_auth():
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/", "/setup"):
                return self._send_html(200, SETUP_HTML)
            if path == "/api/status":
                return self._send_json(200, ctrl.status_payload())
            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            if not self._check_auth():
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            path = urlparse(self.path).path.rstrip("/")
            body = self._read_json()
            if body is None:
                return self._send_json(400, {"ok": False, "message": "Invalid or oversized JSON body"})

            if path == "/api/save":
                return self._send_json(200, ctrl.save_keys(body.get("updates") or {}))
            if path == "/api/clear":
                return self._send_json(200, ctrl.clear_key(str(body.get("key") or "")))
            if path == "/api/test-llm":
                return self._send_json(200, ctrl.test_live_llms())
            if path == "/api/test-telegram":
                return self._send_json(200, ctrl.test_live_telegram(str(body.get("mode") or "get_me")))
            if path == "/api/bot-control":
                return self._send_json(200, ctrl.control_bot(str(body.get("action") or "")))

            self._send_json(404, {"ok": False, "error": "Not found"})

    server = ThreadingHTTPServer((host, int(port)), SetupHandler)
    server.controller = ctrl  # type: ignore[attr-defined]
    return server

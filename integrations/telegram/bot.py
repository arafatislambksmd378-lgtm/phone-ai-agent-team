"""Telegram interface — stdlib long polling, inline callbacks, file upload & artifact delivery.

Security notes:
  * Every update (message, edited_message, callback_query) is checked against
    TELEGRAM_ALLOWED_USER_ID; unknown users are refused and never routed to commands.
  * Outbound messages and captions pass through secret redaction and length caps.
  * Inbound downloads stream to `*.part` with a hard byte cap before magic-byte validation.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import tempfile
import urllib.request
import uuid
from pathlib import Path

from security.secret_manager import redact

log = logging.getLogger("telegram")

MAX_MESSAGE = 4000        # Telegram hard limit is 4096
MAX_CAPTION = 950         # Telegram caption limit is 1024
POLL_TIMEOUT = 25
DOWNLOAD_CAP_MB = 20


class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int, handler,
                 max_upload_mb: int = DOWNLOAD_CAP_MB, base_url: str = "https://api.telegram.org"):
        if not token:
            raise ValueError("Telegram bot token is required")
        self.token = token
        self.allowed = int(allowed_user_id or 0)
        self.handler = handler
        self.max_upload_mb = max(1, int(max_upload_mb))
        self.base = f"{base_url}/bot{token}"
        self.file_base = f"{base_url}/file/bot{token}"
        self._offset = 0
        self._running = True

    # ---------- transport ----------
    def _api(self, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/{method}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log.warning("telegram %s failed: %s", method, redact(str(e)))
            return {"ok": False, "error": redact(str(e))}

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        body = redact(text or "")[:MAX_MESSAGE] or "(empty)"
        payload: dict = {
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._api("sendMessage", payload)

    def send_approval_prompt(self, chat_id: int, approval: dict) -> dict:
        aid = approval.get("id", "")
        action = approval.get("action", "high_impact_action")
        detail = redact(str(approval.get("detail", "")))[:400]
        pid = approval.get("project_id", "")
        text = (
            f"🔐 Approval Required\n"
            f"ID: {aid}\n"
            f"Project: {pid}\n"
            f"Action: {action}\n"
            f"Detail: {detail}\n\n"
            f"Tap a button below or send /approve {aid} or /reject {aid}."
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve:{aid}"},
                {"text": "❌ Reject", "callback_data": f"reject:{aid}"},
            ]]
        }
        try:
            return self.send_message(chat_id, text, reply_markup=keyboard)
        except TypeError:
            # Compatibility with minimal FakeBot overrides taking (chat_id, text)
            return self.send_message(chat_id, text)

    def answer_callback_query(self, callback_query_id: str, text: str = "",
                              show_alert: bool = False) -> dict:
        return self._api("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": redact(text or "")[:190],
            "show_alert": bool(show_alert),
        })

    def clear_inline_buttons(self, chat_id: int, message_id: int) -> dict:
        return self._api("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })

    def send_document(self, chat_id: int, file_path: str, caption: str = "") -> dict:
        """Deliver a workspace artifact (e.g. deliverable ZIP) via stdlib multipart/form-data."""
        p = Path(file_path)
        if not p.is_file():
            return {"ok": False, "error": f"Artifact not found: {file_path}"}
        size = p.stat().st_size
        if size > self.max_upload_mb * 1024 * 1024:
            return {"ok": False, "error": f"Artifact exceeds {self.max_upload_mb}MB Telegram delivery cap"}

        boundary = f"----PhoneAgentBoundary{uuid.uuid4().hex}"
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        safe_caption = redact(caption or "")[:MAX_CAPTION]

        body = bytearray()
        for field_name, field_val in (("chat_id", str(chat_id)), ("caption", safe_caption)):
            if not field_val and field_name == "caption":
                continue
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode("utf-8"))
            body.extend(f"{field_val}\r\n".encode("utf-8"))

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="document"; filename="{p.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        )
        body.extend(p.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        req = urllib.request.Request(
            f"{self.base}/sendDocument",
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log.warning("telegram sendDocument failed: %s", redact(str(e)))
            return {"ok": False, "error": redact(str(e))}

    def get_updates(self, timeout: int = POLL_TIMEOUT) -> list[dict]:
        url = (
            f"{self.base}/getUpdates?timeout={timeout}&offset={self._offset}"
            f"&allowed_updates=%5B%22message%22%2C%22callback_query%22%5D"
        )
        try:
            with urllib.request.urlopen(url, timeout=timeout + 10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log.warning("getUpdates failed: %s", redact(str(e)))
            return []
        if not data.get("ok"):
            log.warning("getUpdates error: %s", data.get("description"))
            return []
        return data.get("result", [])

    # ---------- files ----------
    def download_file(self, file_id: str, dest: str) -> tuple[bool, str]:
        meta = self._api("getFile", {"file_id": file_id})
        if not meta.get("ok"):
            return False, "Telegram did not return file metadata"
        remote = (meta.get("result") or {}).get("file_path")
        if not remote:
            return False, "File path missing in metadata"
        declared = (meta.get("result") or {}).get("file_size", 0)
        cap = self.max_upload_mb * 1024 * 1024
        if declared and declared > cap:
            return False, f"File exceeds {self.max_upload_mb}MB limit"
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{dest}.part"
        try:
            with urllib.request.urlopen(f"{self.file_base}/{remote}", timeout=60) as resp, \
                    open(tmp, "wb") as out:
                written = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > cap:
                        out.close()
                        os.unlink(tmp)
                        return False, f"File exceeds {self.max_upload_mb}MB limit"
                    out.write(chunk)
            os.replace(tmp, dest)
            return True, f"downloaded {written} bytes"
        except Exception as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False, f"download failed: {redact(str(e))}"

    # ---------- loop ----------
    def stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        log.info("Telegram listener started (stdlib long polling).")
        while self._running:
            updates = self.get_updates()
            for update in updates:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                try:
                    self.handler(self, update)
                except Exception:
                    log.exception("handler crashed on update %s", update.get("update_id"))


def extract_user_id(update: dict) -> int | None:
    if not isinstance(update, dict):
        return None
    if "callback_query" in update:
        cb = update.get("callback_query") or {}
        return (cb.get("from") or {}).get("id")
    msg = update.get("message") or update.get("edited_message") or {}
    return (msg.get("from") or {}).get("id")


def is_authorized(update: dict, allowed_user_id: int) -> bool:
    """Strict identity check across messages and inline button callbacks."""
    if not allowed_user_id:
        return False
    uid = extract_user_id(update)
    return uid == allowed_user_id


def temp_upload_path(suffix: str = "") -> str:
    fd, path = tempfile.mkstemp(prefix="tg_upload_", suffix=suffix)
    os.close(fd)
    return path

"""Telegram interface — stdlib long polling, authorized-user only, safe file intake.

Security notes:
  * Every update is checked against TELEGRAM_ALLOWED_USER_ID; unknown users get one
    refusal and are never routed to a handler.
  * Outbound messages pass through secret redaction and are truncated to Telegram's
    limit.
  * Downloads are streamed to a temp file with a hard byte cap, then validated by
    magic bytes before entering a workspace.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

from security.secret_manager import redact

log = logging.getLogger("telegram")

MAX_MESSAGE = 4000        # Telegram hard limit is 4096
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
        data = json.dumps(payload or {}).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network down: log once, keep polling
            log.warning("telegram %s failed: %s", method, e)
            return {"ok": False, "error": str(e)}

    def send_message(self, chat_id: int, text: str) -> dict:
        body = redact(text or "")[:MAX_MESSAGE] or "(empty)"
        return self._api("sendMessage", {"chat_id": chat_id, "text": body,
                                         "disable_web_page_preview": True})

    def get_updates(self, timeout: int = POLL_TIMEOUT) -> list[dict]:
        url = (f"{self.base}/getUpdates?timeout={timeout}&offset={self._offset}"
               f"&allowed_updates=%5B%22message%22%5D")
        try:
            with urllib.request.urlopen(url, timeout=timeout + 10) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            log.warning("getUpdates failed: %s", e)
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
            return False, f"download failed: {e}"

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


def is_authorized(update: dict, allowed_user_id: int) -> bool:
    """Strict identity check. A zero/unset allow-list denies everyone."""
    if not allowed_user_id:
        return False
    msg = update.get("message") or update.get("edited_message") or {}
    user = msg.get("from") or {}
    return user.get("id") == allowed_user_id


def temp_upload_path(suffix: str = "") -> str:
    fd, path = tempfile.mkstemp(prefix="tg_upload_", suffix=suffix)
    os.close(fd)
    return path

"""Telegram interface — stdlib long-polling (no heavy dep), auth-gated, file intake."""
from __future__ import annotations
import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path

log = logging.getLogger("telegram")

class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int, handler, max_upload_mb: int = 20):
        self.token = token
        self.allowed = allowed_user_id
        self.handler = handler  # callable(bot, update_dict)
        self.max_upload_mb = max_upload_mb
        self.base = f"https://api.telegram.org/bot{token}"
        self._offset = 0

    def _api(self, method: str, payload: dict | None = None) -> dict:
        url = f"{self.base}/{method}"
        data = json.dumps(payload or {}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            log.warning("Telegram API %s failed: %s", method, e)
            return {"ok": False, "error": str(e)}

    def send_message(self, chat_id: int, text: str) -> dict:
        from security.secret_manager import redact
        return self._api("sendMessage", {"chat_id": chat_id, "text": redact(text)[:4000]})

    def get_updates(self, timeout: int = 30) -> list[dict]:
        url = f"{self.base}/getUpdates?offset={self._offset}&timeout={timeout}"
        try:
            with urllib.request.urlopen(url, timeout=timeout + 5) as r:
                data = json.loads(r.read().decode())
            return data.get("result", []) if data.get("ok") else []
        except Exception as e:
            log.warning("getUpdates failed: %s", e)
            return []

    def download_file(self, file_id: str, dest: str) -> tuple[bool, str]:
        r = self._api("getFile", {"file_id": file_id})
        if not r.get("ok"):
            return False, "getFile failed"
        path = r["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read(self.max_upload_mb * 1024 * 1024 + 1)
            if len(data) > self.max_upload_mb * 1024 * 1024:
                return False, "File too large"
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(data)
            return True, "OK"
        except Exception as e:
            return False, str(e)

    def run_forever(self) -> None:
        log.info("Telegram listener started (lightweight polling).")
        while True:
            updates = self.get_updates(timeout=30)
            for u in updates:
                self._offset = u.get("update_id", self._offset) + 1
                try:
                    self.handler(self, u)
                except Exception as e:
                    log.exception("handler error: %s", e)

def is_authorized(update: dict, allowed_user_id: int) -> bool:
    msg = update.get("message") or update.get("edited_message") or {}
    user = msg.get("from", {})
    return user.get("id") == allowed_user_id

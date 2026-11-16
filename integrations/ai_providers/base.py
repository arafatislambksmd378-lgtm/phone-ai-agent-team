"""Provider adapter base — stdlib urllib, timeouts, retries, usage."""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error

class ProviderAdapter:
    name: str = "base"
    supports_tools: bool = False
    supports_json: bool = True

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 60):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str) -> dict:
        raise NotImplementedError

    def _post_json(self, url: str, payload: dict, headers: dict | None = None, retries: int = 2) -> dict:
        headers = headers or {}
        data = json.dumps(payload).encode()
        last_err = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {e.read()[:500]}"
                if e.code in (429, 500, 502, 503) and attempt < retries:
                    time.sleep(1 * (attempt + 1))
                    continue
                return {"ok": False, "error": last_err}
            except Exception as e:
                last_err = str(e)
                if attempt < retries:
                    time.sleep(1)
                    continue
                return {"ok": False, "error": last_err}
        return {"ok": False, "error": last_err or "unknown"}

    def _get_json(self, url: str, headers: dict | None = None) -> dict:
        try:
            req = urllib.request.Request(url, headers=headers or {}, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return {"ok": False, "error": str(e)}

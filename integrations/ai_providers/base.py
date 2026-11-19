"""Provider adapter base — stdlib urllib, timeouts, retries, Retry-After, classification.

Result contract (always a dict):
  {"ok": True,  "text": ..., "provider": ..., "model": ...,
   "prompt_tokens": int, "completion_tokens": int}
  {"ok": False, "status": "<classification>", "retryable": bool, "error": "..."}

status classification: NOT_CONFIGURED | AUTH_ERROR | RATE_LIMITED | TIMEOUT |
                       CONNECTION_FAILED | SERVER_ERROR | MALFORMED_RESPONSE |
                       BAD_REQUEST | UNKNOWN
"""
from __future__ import annotations
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("ai_provider")

RETRYABLE = {"RATE_LIMITED", "TIMEOUT", "CONNECTION_FAILED", "SERVER_ERROR"}


class ProviderAdapter:
    name: str = "base"
    supports_tools: bool = False     # advisory: never used to hard-exclude a provider
    supports_json: bool = True

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 60,
                 max_retries: int = 2, max_tokens: int = 4096):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max(256, int(max_tokens or 4096))

    # ---- state ----
    def available(self) -> bool:
        return bool(self.api_key)

    def status(self) -> str:
        return "CONFIGURED" if self.available() else "NOT_CONFIGURED"

    def _redacted(self) -> str:
        if not self.api_key:
            return "(none)"
        return f"{self.api_key[:4]}…{self.api_key[-2:]}" if len(self.api_key) > 8 else "***"

    def __repr__(self) -> str:  # never expose the raw key
        return f"<{self.__class__.__name__} model={self.model!r} key={self._redacted()}>"

    # ---- transport ----
    def _request(self, url: str, payload: dict | None, headers: dict | None = None,
                 method: str = "POST") -> dict:
        """Perform one HTTP attempt and classify the outcome (never echo the key)."""
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "User-Agent": "phone-ai-agent-team/2.0",
                     **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
            try:
                return {"ok": True, "json": json.loads(body.decode())}
            except json.JSONDecodeError:
                return {"ok": False, "status": "MALFORMED_RESPONSE",
                        "error": "Response was not valid JSON"}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read()[:400].decode("utf-8", "replace")
            except Exception:
                pass
            detail = _scrub(detail, self.api_key)
            retry_after = 0.0
            try:
                ra_hdr = (e.headers or {}).get("Retry-After") if getattr(e, "headers", None) else None
                if ra_hdr:
                    retry_after = min(float(ra_hdr), 15.0)
            except (ValueError, TypeError):
                retry_after = 0.0
            if e.code in (401, 403):
                status = "AUTH_ERROR"
            elif e.code == 429:
                status = "RATE_LIMITED"
            elif e.code in (400, 404, 422):
                status = "BAD_REQUEST"
            elif e.code >= 500:
                status = "SERVER_ERROR"
            else:
                status = "UNKNOWN"
            out = {"ok": False, "status": status, "retryable": status in RETRYABLE,
                   "http_status": e.code, "error": f"HTTP {e.code}: {detail}"[:500]}
            if retry_after > 0:
                out["retry_after"] = retry_after
            return out
        except urllib.error.URLError as e:
            reason = str(getattr(e, "reason", e))
            status = "TIMEOUT" if "timed out" in reason.lower() else "CONNECTION_FAILED"
            return {"ok": False, "status": status, "retryable": True,
                    "error": _scrub(reason, self.api_key)[:300]}
        except TimeoutError:
            return {"ok": False, "status": "TIMEOUT", "retryable": True, "error": "Request timed out"}
        except Exception as e:
            return {"ok": False, "status": "UNKNOWN", "retryable": False,
                    "error": _scrub(f"{type(e).__name__}: {e}", self.api_key)[:300]}

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        """POST with bounded exponential backoff (honoring Retry-After) for retryable statuses."""
        last = None
        for attempt in range(self.max_retries + 1):
            last = self._request(url, payload, headers)
            if last.get("ok"):
                return last
            if not last.get("retryable") or attempt == self.max_retries:
                return last
            delay = max(float(last.get("retry_after") or 0.0), float(min(2 ** attempt, 8)))
            delay = min(delay, 15.0)
            log.info("%s: %s — retry %d/%d in %.1fs", self.name, last.get("status"),
                     attempt + 1, self.max_retries, delay)
            time.sleep(delay)
        return last or {"ok": False, "status": "UNKNOWN", "error": "No attempt made"}

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> dict:
        raise NotImplementedError

    # ---- helpers ----
    def _not_configured(self) -> dict:
        return {"ok": False, "status": "NOT_CONFIGURED",
                "error": f"{self.name}: API key not configured"}


def _scrub(text: str, secret: str) -> str:
    """Remove a live key from any error text before it can reach logs/Telegram."""
    if secret and isinstance(text, str) and secret in text:
        text = text.replace(secret, "[REDACTED]")
    return text

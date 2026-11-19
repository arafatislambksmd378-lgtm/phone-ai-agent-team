#!/usr/bin/env python3
"""Real Telegram Bot API checks + Android/Telegram shared-state synchronization.

Runs against a temporary Database/OrchestratorEngine on the GitHub runner. Sends a small
number of real messages to the owner's authorized chat. No credential is printed or stored.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import Settings  # noqa: E402
from integrations.api.server import create_api_server
from integrations.telegram.bot import TelegramBot
from integrations.telegram.handler import TelegramHandler
from orchestrator.engine import OrchestratorEngine
from security.secret_manager import redact

OUT = Path("live-evidence")
RESULTS: list[tuple[str, bool, str]] = []


class RecordingBot(TelegramBot):
    """Real Telegram transport with local records of what was sent (no mocking of transport)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []
        self.answers: list[str] = []
        self.docs: list[dict] = []

    def send_message(self, chat_id, text, reply_markup=None):
        result = super().send_message(chat_id, text, reply_markup)
        self.sent.append(str(text))
        return result

    def answer_callback_query(self, callback_query_id, text="", show_alert=False):
        result = super().answer_callback_query(callback_query_id, text, show_alert)
        self.answers.append(str(text))
        return result

    def send_document(self, chat_id, file_path, caption=""):
        result = super().send_document(chat_id, file_path, caption)
        self.docs.append({"file": Path(file_path).name, "result": bool(result.get("ok"))})
        return result


def step(name: str, fn) -> None:
    try:
        detail = str(fn())
        RESULTS.append((name, True, redact(detail)[:900]))
        print(f"PASS {name}: {redact(detail)[:300]}")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        RESULTS.append((name, False, redact(detail)[:900]))
        print(f"FAIL {name}: {redact(detail)[:300]}")


def message_update(text: str, uid: int, chat: int) -> dict:
    return {
        "update_id": int(time.time() * 1000) % 1_000_000,
        "message": {
            "message_id": 1, "date": 0, "text": text,
            "chat": {"id": chat, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Owner"},
        },
    }


def callback_update(cb_id: str, data: str, uid: int, chat: int) -> dict:
    return {
        "update_id": int(time.time() * 1_000_000) % 1_000_000,
        "callback_query": {
            "id": cb_id, "data": data,
            "from": {"id": uid, "is_bot": False, "first_name": "Owner"},
            "message": {"message_id": 4242, "chat": {"id": chat, "type": "private"}},
        },
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    uid_raw = os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
    if not token or not uid_raw.isdigit():
        print("FAIL: Telegram secrets missing or user id not numeric; no live call made.", file=sys.stderr)
        return 2
    uid = int(uid_raw)

    db_path = OUT / "telegram.db"
    ws = OUT / "tg-workspace"
    settings = Settings(
        telegram_bot_token=token, telegram_allowed_user_id=uid,
        gemini_api_key="", openai_api_key="", codex_api_key="", anthropic_api_key="",
        db_path=db_path, workspace_root=ws,
    )
    engine = OrchestratorEngine(settings)
    bot = RecordingBot(token, uid, None)
    handler = TelegramHandler(engine, settings)
    bot.handler = handler
    handler.bind_bot(bot)

    api_token = secrets.token_urlsafe(32)
    srv = create_api_server(engine.db, host="127.0.0.1", port=0,
                            api_token=api_token, engine=engine)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def api(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body or {}).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as err:
            return err.code, json.load(err)

    state: dict = {}

    def get_me() -> str:
        res = bot._api("getMe")
        assert res.get("ok"), res
        me = res["result"]
        state["bot_username"] = str(me.get("username", ""))
        return f"ok=True username=@{me.get('username', 'unknown')} bot_id={me.get('id')}"

    def unauthorized_message() -> str:
        before = len(engine.db.list_projects())
        handler(bot, message_update("/new Pwned", uid + 1, uid))
        assert bot.sent and "Not authorized" in bot.sent[-1], bot.sent[-1:]
        assert len(engine.db.list_projects()) == before
        return "unauthorized update rejected; no project created"

    def start_help() -> str:
        handler(bot, message_update("/start", uid, uid))
        handler(bot, message_update("/help", uid, uid))
        assert any("Ready" in s for s in bot.sent) and any("commands" in s.lower() for s in bot.sent)
        return "/start and /help delivered"

    def telegram_new() -> str:
        handler(bot, message_update("/new LiveTelegramCheck", uid, uid))
        pid = handler.current.get(uid, "")
        assert pid and engine.db.get_project(pid), handler.sent[-1]
        assert engine.db.get_project(pid)["name"] == "LiveTelegramCheck"
        state["pid"] = pid
        return f"pid={pid} source=telegram"

    def read_commands() -> str:
        pid = state["pid"]
        for cmd in (f"/status {pid}", f"/tasks {pid}", "/agents", f"/pending {pid}"):
            handler(bot, message_update(cmd, uid, uid))
        assert any("LiveTelegramCheck" in s for s in bot.sent)
        assert any("Agents" in s or "Agent" in s for s in bot.sent)
        return "status/tasks/agents/pending commands executed"

    def android_to_telegram() -> str:
        code, created = api("/api/projects", "POST", {"name": "LiveAndroidCheck", "auto_start": False})
        assert code == 201 and created.get("id"), (code, created)
        pid2 = created["id"]
        handler(bot, message_update(f"/status {pid2}", uid, uid))
        assert any("LiveAndroidCheck" in s for s in bot.sent), bot.sent[-3:]
        state["pid2"] = pid2
        return f"Android project {pid2} visible to Telegram"

    def telegram_to_android() -> str:
        pid = state["pid"]
        code, listing = api("/api/projects")
        assert code == 200 and any(p["id"] == pid for p in listing["projects"])
        return f"Telegram project {pid} visible via Android API"

    def task_sync() -> str:
        pid2 = state["pid2"]
        code, task = api(f"/api/projects/{pid2}/tasks", "POST", {
            "title": "Sync cancel check", "agent_id": "qa_tester", "auto_run": False})
        assert code == 201 and task.get("id"), (code, task)
        tid = task["id"]
        handler(bot, message_update(f"/task {tid} cancel", uid, uid))
        code, detail = api(f"/api/projects/{pid2}/tasks/{tid}")
        assert code == 200 and detail["task"]["status"] == "CANCELLED", detail
        events = engine.db.list_sync_events(pid2)
        assert any(e["source"] == "telegram" and e["event"] == "task_cancel" for e in events)
        return f"task={tid} status=CANCELLED visible in API; sync event recorded"

    def approval_callback() -> str:
        pid = state["pid"]
        code, created = api(f"/api/projects/{pid}/approvals", "POST", {
            "action": "external_build", "detail": "harmless callback sync test; no build executed"})
        assert code == 201 and created.get("id"), (code, created)
        aid = created["id"]
        handler(bot, message_update(f"/pending {pid}", uid, uid))
        handler(bot, callback_update("cb-live-1", f"approve:{aid}", uid, uid))
        assert engine.db.get_approval(aid)["status"] == "APPROVED", engine.db.get_approval(aid)
        events = engine.db.list_sync_events(pid)
        assert any(e["source"] == "telegram" and e["event"] == "approval_approved" for e in events)
        state["aid"] = aid
        return f"approval={aid} approved via inline callback; Android API sees same row"

    def duplicate_callback() -> str:
        handler(bot, callback_update("cb-live-1", f"approve:{state['aid']}", uid, uid))
        assert any("Duplicate" in a for a in bot.answers), bot.answers[-3:]
        return "duplicate callback_query id ignored"

    def unauthorized_callback() -> str:
        pid = state["pid"]
        code, created = api(f"/api/projects/{pid}/approvals", "POST", {
            "action": "external_build", "detail": "unauthorized callback probe; must stay pending"})
        aid = created["id"]
        handler(bot, callback_update("cb-live-bad", f"approve:{aid}", uid + 1, uid))
        assert engine.db.get_approval(aid)["status"] == "PENDING", engine.db.get_approval(aid)
        logs = engine.db.list_approval_audit_logs(pid, limit=50)
        assert any("unauthorized" in str(row).lower() for row in logs), logs[-3:]
        return "unauthorized callback rejected; approval still PENDING"

    def artifact_and_memory() -> str:
        pid = state["pid"]
        handler(bot, message_update(f"/artifact {pid}", uid, uid))
        assert bot.docs, "no sendDocument call recorded"
        code, _ = api(f"/api/projects/{pid}/memory", "POST", {
            "kind": "decision", "key": "live_sync", "value": "shared backend verified"})
        assert code == 200, code
        handler(bot, message_update(f"/sync {pid}", uid, uid))
        assert any("memory_written" in s for s in bot.sent), bot.sent[-5:]
        code, memory = api(f"/api/projects/{pid}/memory?kind=decision")
        assert code == 200 and memory["entries"][0]["key"] == "live_sync", memory
        return "ZIP delivered; memory written via API and visible in Telegram activity"

    def optional_build() -> str:
        if os.getenv("RUN_AGENT_BUILD", "").strip().lower() not in {"1", "true", "yes"}:
            return "SKIPPED: workflow input run_agent_build was false"
        handler(bot, message_update(f"/build {state['pid2']}", uid, uid))
        deadline = time.time() + 240
        while time.time() < deadline:
            tasks = engine.db.list_tasks(state["pid2"])
            if tasks and all(t["status"] in {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"} for t in tasks):
                break
            time.sleep(2)
        done = sum(1 for t in engine.db.list_tasks(state["pid2"]) if t["status"] == "COMPLETED")
        assert done > 0, engine.db.list_tasks(state["pid2"])
        return f"/build executed; completed_tasks={done}"

    step("Telegram getMe (real Bot API)", get_me)
    step("unauthorized user rejected", unauthorized_message)
    step("/start and /help", start_help)
    step("/new project creation (Telegram)", telegram_new)
    step("/status /tasks /agents /pending", read_commands)
    step("Android-created project visible in Telegram", android_to_telegram)
    step("Telegram-created project visible via Android API", telegram_to_android)
    step("task cancel sync (Android API → Telegram → API)", task_sync)
    step("inline approval callback sync", approval_callback)
    step("duplicate callback protection", duplicate_callback)
    step("unauthorized callback rejected", unauthorized_callback)
    step("artifact delivery + shared memory/activity", artifact_and_memory)
    step("/build execution (optional)", optional_build)

    lines = ["# Live Telegram / Android synchronization verification", "",
             f"Bot username: @{state.get('bot_username', 'unknown')} (from real getMe)", "",
             "Messages were sent to the authorized owner chat. Transport was real;",
             "incoming updates were synthetic to keep the test deterministic.", ""]
    for name, passed, detail in RESULTS:
        lines.append(f"- {'PASS' if passed else 'FAIL'} {name}: {detail}")
    (OUT / "telegram.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    srv.shutdown()
    srv.server_close()
    thread.join(timeout=10)
    engine.shutdown()
    ok = all(passed for _, passed, _ in RESULTS)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} — {sum(p for _, p, _ in RESULTS)}/{len(RESULTS)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

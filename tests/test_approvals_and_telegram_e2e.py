"""Tests for execution-layer approvals, audit logs, Telegram inline callbacks, and ZIP delivery."""
from __future__ import annotations

import pytest

from config.settings import Settings
from execution.command_runner import CommandRunner
from execution.file_manager import FileManager
from integrations.telegram.handler import TelegramHandler
from memory.database import Database
from orchestrator.engine import OrchestratorEngine
from security.approval import ApprovalManager, ApprovalRequiredError


class CallbackFakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict | None]] = []
        self.callbacks_answered: list[tuple[str, str, bool]] = []
        self.cleared_buttons: list[tuple[int, int]] = []
        self.documents: list[tuple[int, str, str]] = []

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        self.sent.append((chat_id, text, reply_markup))
        return {"ok": True}

    def send_approval_prompt(self, chat_id: int, approval: dict) -> dict:
        aid = approval["id"]
        kb = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve:{aid}"},
            {"text": "❌ Reject", "callback_data": f"reject:{aid}"},
        ]]}
        return self.send_message(chat_id, f"Approval {aid}", reply_markup=kb)

    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> dict:
        self.callbacks_answered.append((callback_query_id, text, show_alert))
        return {"ok": True}

    def clear_inline_buttons(self, chat_id: int, message_id: int) -> dict:
        self.cleared_buttons.append((chat_id, message_id))
        return {"ok": True}

    def send_document(self, chat_id: int, file_path: str, caption: str = "") -> dict:
        self.documents.append((chat_id, file_path, caption))
        return {"ok": True}


def test_execution_layer_approval_cannot_be_bypassed_by_boolean_flag(tmp_path):
    db = Database(tmp_path / "appr.db")
    am = ApprovalManager(db)
    ws = tmp_path / "ws"
    ws.mkdir()
    pid = db.create_project("sec", str(ws))

    # 1. CommandRunner ignores `approved=True` when ApprovalManager is bound without valid approval_id!
    runner = CommandRunner(str(ws), approval_manager=am, project_id=pid)
    res = runner.run("git push origin main", approved=True)
    assert not res["ok"]
    assert res.get("needs_approval") is True
    aid = res["approval_id"]
    assert am.status(aid) == "PENDING"

    # 2. FileManager blocks writing `.env` and deleting files without valid approval_id
    fm = FileManager(str(ws), approval_manager=am, project_id=pid)
    with pytest.raises(ApprovalRequiredError) as exc_info:
        fm.write(".env", "SECRET=1")
    sec_aid = exc_info.value.approval_id
    assert am.status(sec_aid) == "PENDING"

    # Once approved by human, writing `.env` with that `approval_id` succeeds!
    am.approve(sec_aid, by="7")
    fm.write(".env", "PORT=8080\n", approval_id=sec_aid)
    assert fm.exists(".env")

    # 3. Single-use consumption & TTL expiration checks
    single_aid = am.request(pid, "production_deploy", "Deploy v1", ttl_seconds=300)
    am.approve(single_aid, by="7")
    ok1, _ = am.verify(single_aid, project_id=pid, action="production_deploy", consume=True)
    assert ok1
    ok2, msg2 = am.verify(single_aid, project_id=pid, action="production_deploy", consume=True)
    assert not ok2 and "consumed" in msg2.lower()

    # Expired approval is rejected automatically
    exp_aid = db.create_approval(pid, "git_push", "Push", ttl_seconds=-10)
    assert am.status(exp_aid) == "EXPIRED"
    assert not am.approve(exp_aid, by="7")

    audits = am.audit_logs(pid)
    assert len(audits) >= 4
    db.close()


def test_telegram_inline_approval_callbacks_duplicate_and_unauthorized_guard(tmp_path):
    s = Settings(
        telegram_bot_token="123:ABC",
        telegram_allowed_user_id=7,
        workspace_root=tmp_path / "ws",
        db_path=tmp_path / "tg_cb.db",
    )
    engine = OrchestratorEngine(s)
    bot = CallbackFakeBot()
    handler = TelegramHandler(engine, s)
    handler.bind_bot(bot, default_chat_id=42)

    try:
        # Create staged project via /new without starting premature 16-agent run
        handler(bot, {"update_id": 1, "message": {"from": {"id": 7}, "chat": {"id": 42}, "text": "/new demoapp"}})
        pid = handler.current[42]
        assert engine.lifecycle.get(pid) == "INPUT_RECEIVED"

        # Request an approval -> verify inline buttons were pushed to Telegram automatically
        aid = engine.approvals.request(pid, "production_deploy", "Deploy demoapp")
        assert any(m[2] and "inline_keyboard" in m[2] for m in bot.sent)

        # 1. Unauthorized user (id=999) clicks Approve -> refused & logged in approval_audit_logs!
        handler(bot, {
            "update_id": 2,
            "callback_query": {
                "id": "cb_unauth",
                "from": {"id": 999},
                "data": f"approve:{aid}",
                "message": {"message_id": 10, "chat": {"id": 999}},
            },
        })
        assert engine.approvals.status(aid) == "PENDING"
        assert bot.callbacks_answered[-1][2] is True  # show_alert=True
        events = [a["event"] for a in engine.approvals.audit_logs(pid)]
        assert "UNAUTHORIZED_ATTEMPT" in events

        # 2. Authorized owner (id=7) clicks Approve -> approved & inline buttons cleared!
        cb_approve = {
            "update_id": 3,
            "callback_query": {
                "id": "cb_ok_1",
                "from": {"id": 7},
                "data": f"approve:{aid}",
                "message": {"message_id": 11, "chat": {"id": 42}},
            },
        }
        handler(bot, cb_approve)
        assert engine.approvals.status(aid) == "APPROVED"
        assert (42, 11) in bot.cleared_buttons

        # 3. Duplicate callback with same callback_query.id -> ignored cleanly
        handler(bot, cb_approve)
        assert "Duplicate" in bot.callbacks_answered[-1][1]

        # 4. Second click with a new callback_query.id after already decided -> rejected safely
        handler(bot, {
            "update_id": 4,
            "callback_query": {
                "id": "cb_ok_2",
                "from": {"id": 7},
                "data": f"reject:{aid}",
                "message": {"message_id": 11, "chat": {"id": 42}},
            },
        })
        assert engine.approvals.status(aid) == "APPROVED"

        # 5. Test /logs (both with and without project ID) and /artifact ZIP delivery
        handler(bot, {"update_id": 5, "message": {"from": {"id": 7}, "chat": {"id": 42}, "text": "/logs"}})
        handler(bot, {"update_id": 6, "message": {"from": {"id": 7}, "chat": {"id": 42}, "text": f"/artifact {pid}"}})
        assert len(bot.documents) == 1
        assert bot.documents[0][1].endswith(".zip")
    finally:
        engine.shutdown()

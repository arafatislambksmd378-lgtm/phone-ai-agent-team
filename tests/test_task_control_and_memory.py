"""Durable task control (P0/P1) and versioned project memory (P1).

All tests are local: no live provider, Telegram, GitHub or worker calls.
"""
import json
import sqlite3
import threading
import time
import urllib.request

import pytest

from config.settings import Settings
from integrations.api.server import create_api_server
from integrations.telegram.handler import TelegramHandler
from memory.database import Database
from orchestrator.engine import OrchestratorEngine
from tests.test_phase3_security import TOKEN, request
from tests.test_telegram import FakeBot, update


def _engine(tmp_path):
    settings = Settings(
        telegram_bot_token="123:ABC", telegram_allowed_user_id=7,
        workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db",
    )
    return OrchestratorEngine(settings)


def test_v6_migration_preserves_legacy_data_and_adds_columns(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'INPUT_RECEIVED', lifecycle TEXT NOT NULL DEFAULT 'IDLE',
          workspace TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          metadata TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
          agent_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
          priority INTEGER NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}',
          result TEXT, output_data TEXT NOT NULL DEFAULT '{}',
          acceptance_criteria TEXT NOT NULL DEFAULT '[]', error TEXT,
          retries INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 3,
          locked_by TEXT, locked_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        INSERT INTO migrations VALUES (5, 0);
        INSERT INTO projects VALUES ('p1','legacy','IDLE','IDLE','.',0,0,'{}');
        INSERT INTO tasks(id,project_id,agent_id,title,status,created_at,updated_at)
          VALUES ('t1','p1','qa_tester','old work','QUEUED',0,0);
    """)
    con.commit()
    con.close()

    db = Database(path)
    assert db.get_project("p1")["name"] == "legacy"
    cols = {r[1] for r in db.execute("PRAGMA table_info(tasks)").fetchall()}
    assert {"cancel_requested", "not_before", "idempotency_key"} <= cols
    assert db.query_one("SELECT version FROM migrations WHERE version=6")["version"] == 6
    assert db.claim_task("worker")["id"] == "t1"  # legacy task remains schedulable
    db.close()


def test_cancel_queued_task_is_durable_and_never_claimed(tmp_path):
    path = tmp_path / "cancel.db"
    db = Database(path)
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "cancel me")
    res = db.request_task_cancel(tid, actor="owner")
    assert res["effective"] == "cancelled"
    assert db.get_task(tid)["status"] == "CANCELLED"
    assert db.claim_task("w1") is None
    db.close()

    reopened = Database(path)
    assert reopened.get_task(tid)["status"] == "CANCELLED"
    assert reopened.claim_task("w2") is None
    assert reopened.list_task_control_events(tid)[0]["action"] == "cancel_requested"
    reopened.close()


def test_cancel_running_task_survives_crash_recovery(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "running")
    assert db.claim_task("w1")["id"] == tid
    res = db.request_task_cancel(tid, actor="telegram")
    assert res["effective"] == "pending" and res["status"] == "EXECUTING"
    assert db.claim_task("w2") is None
    # Simulate a backend restart while the old worker was still marked RUNNING.
    assert db.recover_interrupted() == 1
    assert db.get_task(tid)["status"] == "CANCELLED"
    assert db.claim_task("w3") is None
    db.close()


def test_retry_backoff_is_enforced_by_atomic_claim(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "flaky", max_retries=3)
    assert db.claim_task("w1")["id"] == tid
    assert db.requeue_with_backoff(tid, 30, "rate limited") is True
    task = db.get_task(tid)
    assert task["status"] == "QUEUED" and task["not_before"] > time.time() + 25
    assert db.claim_task("w2") is None
    db.execute("UPDATE tasks SET not_before=? WHERE id=?", (time.time() - 1, tid))
    assert db.claim_task("w2")["id"] == tid
    db.close()


def test_retry_and_resume_rules_prevent_duplicate_completed_work(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    done = db.create_task(pid, "release_manager", "already released")
    db.set_task_status(done, "COMPLETED", result="v1")
    assert db.requeue_task(done, "retry", "owner")["ok"] is False
    assert db.get_task(done)["status"] == "COMPLETED"

    failed = db.create_task(pid, "qa_tester", "broken")
    db.set_task_status(failed, "FAILED", error="boom")
    db.execute("UPDATE tasks SET retries=3 WHERE id=?", (failed,))
    assert db.requeue_task(failed, "retry", "owner")["status"] == "QUEUED"
    assert db.get_task(failed)["retries"] == 0

    cancelled = db.create_task(pid, "devops", "stopped")
    db.request_task_cancel(cancelled, actor="owner")
    assert db.requeue_task(cancelled, "resume", "owner")["status"] == "QUEUED"
    assert db.get_task(cancelled)["cancel_requested"] == 0
    assert len({e["action"] for e in db.list_task_control_events(cancelled)}) >= 2
    db.close()


def test_structured_memory_is_deduped_versioned_isolated_and_bounded(tmp_path):
    db = Database(tmp_path / "t.db")
    pid_a = db.create_project("a", str(tmp_path))
    pid_b = db.create_project("b", str(tmp_path))

    first = db.remember_entry(pid_a, "decision", "database", "SQLite WAL", source="architect")
    same = db.remember_entry(pid_a, "decision", "database", "SQLite WAL", source="architect")
    assert first["version"] == 1 and same["deduped"] is True
    second = db.remember_entry(pid_a, "decision", "database", "PostgreSQL", source="owner")
    assert second["version"] == 2
    history = db.memory_entry_history(pid_a, "decision", "database")
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["superseded_by"] is None and history[1]["superseded_by"] == second["id"]

    db.remember_entry(pid_a, "requirement", "offline", "must work without network")
    assert [e["key"] for e in db.search_memory_entries(pid_a, kind="decision")] == ["database"]
    assert [e["key"] for e in db.search_memory_entries(pid_a, query="offline")] == ["offline"]
    assert db.search_memory_entries(pid_b, query="SQLite") == []  # project isolation

    for i in range(5):
        db.remember_entry(pid_a, "note", "bounded", f"v{i}", max_versions=2)
    assert len(db.memory_entry_history(pid_a, "note", "bounded")) == 2
    db.close()


def test_engine_control_task_writes_shared_sync_events(tmp_path):
    engine = _engine(tmp_path)
    pid = engine.create_project("shared", "build something", auto_start=False)
    tid = engine.db.create_task(pid, "qa_tester", "task")
    res = engine.control_task(pid, tid, "cancel", actor="telegram")
    assert res["ok"] and res["status"] == "CANCELLED"
    assert engine.db.list_sync_events(pid)[0]["source"] == "telegram"
    assert engine.status(pid)["tasks"][0]["cancel_requested"] is True
    engine.shutdown()


def test_owner_cancelled_task_does_not_finish_as_completed_project(tmp_path):
    engine = _engine(tmp_path)
    pid = engine.create_project("cancel-project", "blueprint", auto_start=False)
    tid = engine.db.create_task(pid, "qa_tester", "only task")
    engine.control_task(pid, tid, "cancel", actor="owner")
    assert engine.db.get_task(tid)["status"] == "CANCELLED"
    assert engine.lifecycle.get(pid) == "CANCELLED"
    engine.shutdown()


def test_telegram_task_command_controls_one_task(tmp_path):
    engine = _engine(tmp_path)
    handler = TelegramHandler(engine, Settings(telegram_allowed_user_id=7))
    bot = FakeBot()
    pid = engine.create_project("telegram", "task control", auto_start=False)
    tid = engine.db.create_task(pid, "qa_tester", "one task")

    handler(bot, update(7, f"/task {tid}"))
    assert "QUEUED" in bot.sent[-1][1]
    handler(bot, update(7, f"/task {tid} cancel"))
    assert "cancel" in bot.sent[-1][1].lower()
    assert engine.db.get_task(tid)["status"] == "CANCELLED"
    events = engine.db.list_sync_events(pid)
    assert any(e["source"] == "telegram" and e["event"] == "task_cancel" for e in events)
    engine.shutdown()


def test_api_control_and_memory_endpoints_are_auth_gated_and_idempotent(tmp_path):
    engine = _engine(tmp_path)
    engine.scheduler.start = lambda: None  # no background execution in this API test
    srv = create_api_server(engine.db, port=0, api_token=TOKEN, engine=engine)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, proj = request(srv, "/api/projects", TOKEN, "POST",
                               json.dumps({"name": "ApiProject", "auto_start": False}).encode())
        assert status == 201
        pid = proj["id"]
        status, task = request(srv, f"/api/projects/{pid}/tasks", TOKEN, "POST",
                               json.dumps({"title": "Cancel from Android", "agent_id": "qa_tester",
                                           "auto_run": False}).encode())
        assert status == 201
        tid = task["id"]

        body = json.dumps({"action": "cancel", "actor": "android",
                           "idempotency_key": "tap-1"}).encode()
        status, res = request(srv, f"/api/projects/{pid}/tasks/{tid}/control", TOKEN, "POST", body)
        assert status == 200 and res["status"] == "CANCELLED"
        status, again = request(srv, f"/api/projects/{pid}/tasks/{tid}/control", TOKEN, "POST", body)
        assert status == 200 and again["deduplicated"] is True
        assert len([e for e in engine.db.list_task_control_events(tid)
                    if e["action"] == "cancel_requested"]) == 1

        status, detail = request(srv, f"/api/projects/{pid}/tasks/{tid}", TOKEN)
        assert status == 200 and detail["control_events"][0]["actor"] == "android"

        status, written = request(srv, f"/api/projects/{pid}/memory", TOKEN, "POST",
                                  json.dumps({"kind": "decision", "key": "storage",
                                              "value": "SQLite WAL"}).encode())
        assert status == 200 and written["version"] == 1
        status, memory = request(srv, f"/api/projects/{pid}/memory?kind=decision", TOKEN)
        assert status == 200 and memory["entries"][0]["key"] == "storage"
        assert engine.db.get_memory(pid)["decision:storage"] == "SQLite WAL"
        assert request(srv, f"/api/projects/{pid}/memory")[0] == 401  # never unauthenticated
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()
        engine.shutdown()

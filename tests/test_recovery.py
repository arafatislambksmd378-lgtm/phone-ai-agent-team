"""Recovery, retries, and crash-safety."""
import time

from config.settings import Settings
from memory.database import Database
from orchestrator.engine import OrchestratorEngine


def test_recover_interrupted_requeues_and_closes_executions(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "t")
    db.claim_task("w1")
    eid = db.start_execution(pid, tid, "qa_tester")
    assert db.recover_interrupted() == 1
    assert db.list_tasks(pid)[0]["status"] == "QUEUED"
    assert db.query_one("SELECT status FROM executions WHERE id=?", (eid,))["status"] == "INTERRUPTED"
    db.close()


def test_retry_then_permanent_failure(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "t", max_retries=2)
    for expected in (1, 2, 3):
        assert db.increment_retry(tid) == expected
    assert db.list_tasks(pid)[0]["max_retries"] == 2
    db.close()


def test_requeue_failed_only_below_max_retries(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    ok_task = db.create_task(pid, "qa_tester", "recoverable")
    dead_task = db.create_task(pid, "devops", "exhausted", max_retries=1)
    db.set_task_status(ok_task, "FAILED", error="boom")
    db.set_task_status(dead_task, "FAILED", error="boom")
    db.execute("UPDATE tasks SET retries=1 WHERE id=?", (dead_task,))
    assert db.requeue_failed(pid) == 1
    statuses = {t["id"]: t["status"] for t in db.list_tasks(pid)}
    assert statuses[ok_task] == "QUEUED"
    assert statuses[dead_task] == "FAILED"
    db.close()


def test_recover_endpoint_requeues_failed_project(tmp_path):
    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1,
                 workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db")
    engine = OrchestratorEngine(s)
    pid = engine.create_project("p", "req", quick=False)
    # Force a failure state
    engine.scheduler.stop()
    for t in engine.db.list_tasks(pid):
        engine.db.set_task_status(t["id"], "FAILED", error="forced")
    engine.db.set_project_status(pid, "FAILED", lifecycle="FAILED")
    engine.lifecycle._states[pid] = "FAILED"
    res = engine.recover(pid)
    assert res["ok"] and res["requeued"] > 0
    engine.cancel(pid)
    engine.shutdown()


def test_database_survives_reopen(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    pid = db.create_project("p", str(tmp_path))
    db.create_task(pid, "qa_tester", "persisted")
    db.close()
    again = Database(path)
    assert again.get_project(pid)["name"] == "p"
    assert len(again.list_tasks(pid)) == 1
    again.close()


def test_online_backup_is_a_valid_database(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("p", str(tmp_path))
    dest = tmp_path / "backup.db"
    db.backup(str(dest))
    restored = Database(dest)
    assert restored.get_project(pid) is not None
    restored.close()
    db.close()

"""Scheduler, dependency and worker-shutdown behaviour (regression suite)."""
import time

import pytest

from config.settings import Settings
from memory.database import Database
from orchestrator.engine import OrchestratorEngine


def make_engine(tmp_path, **kw):
    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1,
                 workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db", **kw)
    return OrchestratorEngine(s)


def wait_final(engine, pid, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = engine.status(pid)["lifecycle"]
        if state in ("COMPLETED", "FAILED", "CANCELLED"):
            return state
        time.sleep(0.2)
    return engine.status(pid)["lifecycle"]


def test_last_task_is_not_marked_failed(tmp_path):
    """Regression: stopping the pool from inside a worker used to kill the final task."""
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "Build a todo app.", quick=True)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
        tasks = engine.db.list_tasks(pid)
        assert all(t["status"] == "COMPLETED" for t in tasks), \
            [(t["agent_id"], t["status"], t["error"]) for t in tasks]
        assert engine.db.get_project(pid)["status"] == "COMPLETED"
    finally:
        engine.shutdown()


def test_workers_stop_after_completion(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "Build a todo app.", quick=True)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
        time.sleep(1.0)
        assert engine.scheduler.active_count() == 0
        assert not engine.scheduler.is_running()
    finally:
        engine.shutdown()


def test_full_pipeline_runs_all_16(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("full", "Build a todo app with login.", quick=False)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
        tasks = engine.db.list_tasks(pid)
        assert len(tasks) == 16
        assert {t["agent_id"] for t in tasks} == {
            "chief_orchestrator", "requirement_analyst", "software_architect", "uiux_designer",
            "database_engineer", "backend_developer", "frontend_developer", "api_integrator",
            "security_engineer", "qa_tester", "debugger", "code_reviewer", "devops",
            "documentation", "release_manager", "workspace_executor"}
        assert all(t["status"] == "COMPLETED" for t in tasks)
    finally:
        engine.shutdown()


def test_dependency_ordering_is_respected(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "req", quick=True)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
        rows = engine.db.query(
            "SELECT t.agent_id, t.status, t.created_at FROM tasks t WHERE t.project_id=? "
            "ORDER BY t.priority", (pid,))
        assert [r["agent_id"] for r in rows][0] == "requirement_analyst"
        for sql_dep in engine.db.query(
                "SELECT d.task_id, d.depends_on, p.status FROM task_dependencies d "
                "JOIN tasks p ON p.id=d.depends_on"):
            assert sql_dep["status"] == "COMPLETED"
    finally:
        engine.shutdown()


def test_claimed_task_is_never_handed_out_twice(tmp_path):
    db = Database(tmp_path / "q.db")
    pid = db.create_project("p", str(tmp_path))
    tid = db.create_task(pid, "qa_tester", "one task")
    first = db.claim_task("w1")
    second = db.claim_task("w2")
    assert first["id"] == tid
    assert second is None
    db.close()


def test_planning_twice_does_not_duplicate(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "req", quick=True)
    try:
        before = len(engine.db.list_tasks(pid))
        engine.planner.plan(pid, "req", quick=True)
        assert len(engine.db.list_tasks(pid)) == before
    finally:
        engine.shutdown()


def test_lifecycle_is_persisted_across_restart(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "req", quick=True)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
    finally:
        engine.shutdown()
    fresh = make_engine(tmp_path)
    try:
        assert fresh.status(pid)["lifecycle"] == "COMPLETED"
    finally:
        fresh.shutdown()


def test_cancel_marks_tasks(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "req", quick=False)
    try:
        engine.cancel(pid)
        assert engine.db.get_project(pid)["lifecycle"] == "CANCELLED"
        assert all(t["status"] == "CANCELLED"
                   for t in engine.db.list_tasks(pid)
                   if t["status"] != "COMPLETED")
    finally:
        engine.shutdown()


def test_illegal_lifecycle_transition_is_refused(tmp_path):
    engine = make_engine(tmp_path)
    pid = engine.create_project("p", "req", quick=True)
    try:
        assert wait_final(engine, pid) == "COMPLETED"
        assert engine.lifecycle.set(pid, "EXECUTING") is False   # COMPLETED is terminal
        assert engine.lifecycle.get(pid) == "COMPLETED"
    finally:
        engine.shutdown()

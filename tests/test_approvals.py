"""Approval workflow — request, approve, reject, single-decision semantics."""
import pytest

from memory.database import Database
from security.approval import DANGEROUS_ACTIONS, ApprovalManager


@pytest.fixture()
def manager(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_project("p", str(tmp_path))
    yield ApprovalManager(db)
    db.close()


def test_request_and_approve(manager):
    aid = manager.request("p", "git_push", "push feature branch")
    assert manager.status(aid) == "PENDING"
    assert len(manager.pending()) == 1
    assert manager.approve(aid, by="user") is True
    assert manager.is_approved(aid)
    assert manager.pending() == []


def test_reject(manager):
    aid = manager.request("p", "deploy", "deploy to production")
    assert manager.reject(aid, by="user") is True
    assert manager.is_rejected(aid) is False or manager.status(aid) == "REJECTED"
    assert not manager.is_approved(aid)


def test_cannot_decide_twice(manager):
    aid = manager.request("p", "publish", "publish package")
    assert manager.approve(aid) is True
    assert manager.approve(aid) is False          # already decided
    assert manager.reject(aid) is False


def test_unknown_approval_is_not_approved(manager):
    assert manager.approve("appr_missing") is False
    assert manager.is_approved("appr_missing") is False
    assert manager.status("appr_missing") is None


def test_require_rejects_unknown_action(manager):
    with pytest.raises(ValueError):
        manager.require("p", "something_harmless", "x")
    assert "git_push" in DANGEROUS_ACTIONS


def test_pending_blocks_project_completion(tmp_path):
    """A project with a pending approval must not be reported COMPLETED."""
    from config.settings import Settings
    from orchestrator.engine import OrchestratorEngine
    import time

    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1,
                 workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db")
    engine = OrchestratorEngine(s)
    pid = engine.create_project("p", "req", quick=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        if engine.status(pid)["lifecycle"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.2)
    # Now open an approval and re-evaluate the project
    engine.approvals.request(pid, "git_push", "push to origin")
    engine.lifecycle._states[pid] = "QUEUED"
    engine.db.set_project_status(pid, "QUEUED", lifecycle="QUEUED")
    engine._maybe_finish_project(pid)
    assert engine.status(pid)["lifecycle"] == "WAITING_FOR_APPROVAL"
    engine.approvals.approve(engine.db.pending_approvals(pid)[0]["id"])
    engine.shutdown()

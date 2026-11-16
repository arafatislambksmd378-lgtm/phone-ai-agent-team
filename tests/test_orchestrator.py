import tempfile, os
from config.settings import Settings
from orchestrator.engine import OrchestratorEngine
import time

def test_quick_pipeline_completes(tmp_path):
    s = Settings(telegram_bot_token="x", telegram_allowed_user_id=1,
                 workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db",
                 max_concurrent_workers=1)
    eng = OrchestratorEngine(s)
    pid = eng.create_project("demo", requirements="Build a todo app. Must be fast.", quick=True)
    # Wait for completion (max 30s)
    deadline = time.time() + 30
    while time.time() < deadline:
        st = eng.status(pid)
        if st["lifecycle"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.5)
    st = eng.status(pid)
    assert st["lifecycle"] == "COMPLETED", st
    assert len(st["tasks"]) == 5
    eng.shutdown()

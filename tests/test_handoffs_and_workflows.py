"""Tests for 16-agent contracts, verified handoffs, AI codegen, debugger repair, and multi-workflow E2E."""
from __future__ import annotations

import time
from pathlib import Path

from agents.base import AgentResult
from agents.registry import AGENT_MODULES, AgentRegistry
from config.settings import Settings
from execution.file_manager import FileManager
from memory.database import Database
from orchestrator.engine import OrchestratorEngine
from orchestrator.handoff import HandoffManager


def _wait_project(engine: OrchestratorEngine, pid: str, timeout: float = 45.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = engine.status(pid)
        if st["lifecycle"] in ("COMPLETED", "FAILED", "CANCELLED", "WAITING_FOR_APPROVAL"):
            return st
        time.sleep(0.15)
    return engine.status(pid)


def test_all_16_agents_expose_complete_contracts(tmp_path):
    reg = AgentRegistry(workspace=str(tmp_path))
    assert len(AGENT_MODULES) == 16
    for aid in AGENT_MODULES:
        agent = reg.get(aid)
        c = agent.contract()
        assert isinstance(c, dict)
        assert c["agent_id"] == aid
        assert len(agent.system_prompt.strip()) >= 80
        assert len(c["allowed_tools"]) >= 1
        assert isinstance(c["output_schema"], dict)
        assert len(c["completion_criteria"]) >= 1


def test_handoff_verification_and_failure_propagation(tmp_path):
    db = Database(tmp_path / "handoff.db")
    ws = tmp_path / "ws"
    ws.mkdir()
    fm = FileManager(str(ws))
    hm = HandoffManager(db)

    pid = db.create_project("ho", str(ws))
    t1 = db.create_task(pid, "requirement_analyst", "Analyze", {"requirements": "Build CLI"}, priority=10)
    t2 = db.create_task(pid, "software_architect", "Architect", {}, depends_on=[t1], priority=20)
    t3 = db.create_task(pid, "backend_developer", "Develop", {}, depends_on=[t2], priority=30)

    # Write real file + hallucinated missing file -> only real file passes SHA-256 verification
    fm.write("spec/requirements_spec.json", '{"software_type": "cli_tool"}')
    res1 = AgentResult(
        ok=True,
        summary="Spec generated",
        files=["spec/requirements_spec.json", "spec/nonexistent.json"],
        data={"fr_count": 3},
    )
    task1_row = db.get_task(t1)
    env = hm.verify_and_record(pid, task1_row, res1, fm)
    assert len(env.verified_files) == 1
    assert env.verified_files[0]["path"] == "spec/requirements_spec.json"
    assert len(env.verified_files[0]["sha256"]) == 64
    assert env.missing_files == ["spec/nonexistent.json"]
    db.set_task_status(t1, "COMPLETED", result=res1.summary, output_data=env.to_dict())

    # Downstream task t2 receives verified upstream handoff from t1
    t2_input = hm.prepare_task_input(pid, db.get_task(t2))
    assert "requirement_analyst" in t2_input["upstream_handoffs"]
    assert t2_input["upstream_handoffs"]["requirement_analyst"]["output"]["data"]["fr_count"] == 3

    # If t2 permanently fails, t3 is automatically marked SKIPPED
    db.set_task_status(t2, "FAILED", error="Architecture validation error")
    skipped = hm.propagate_failure(pid)
    assert skipped == 1
    assert db.get_task(t3)["status"] == "SKIPPED"
    db.close()


def test_ai_powered_custom_code_generation_and_debugger_repair_loop(tmp_path):
    """Verify real AI multi-file generation (non-CRUD business logic) + Debugger fixing a real bug."""
    ws = tmp_path / "calc_ws"
    ws.mkdir()
    fm = FileManager(str(ws))

    class StubLiveRouter:
        def __init__(self, reply_text: str):
            self.reply_text = reply_text

        def mode(self) -> str:
            return "live"

        def complete(self, agent_id: str, system: str, user: str, **kwargs) -> dict:
            return {
                "ok": True,
                "text": self.reply_text,
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "mode": "live",
                "mock": False,
                "prompt_tokens": 120,
                "completion_tokens": 180,
            }

    # 1. BackendDeveloper generates custom Loan business logic from AI code block (with an intentional bug)
    buggy_ai_code = (
        "```python\n"
        "def simple_interest(principal: float, rate_pct: float, years: float) -> float:\n"
        "    # Intentional bug: multiplies instead of dividing by 100\n"
        "    return round(principal * rate_pct * years, 2)\n"
        "```\n"
    )
    reg_buggy = AgentRegistry(workspace=str(ws), ai_router=StubLiveRouter(buggy_ai_code), file_manager=fm)
    dev_res = reg_buggy.run("backend_developer", {
        "payload": {
            "requirements": "Create a Python finance library computing simple_interest(principal, rate_pct, years).",
            "software_type": "python_library",
            "blueprint_analysis": {"software_type": "python_library"},
        }
    })
    assert dev_res.ok, dev_res.error
    assert dev_res.mode == "live"
    pkg_dir = dev_res.data["package"]
    assert fm.exists(f"src/{pkg_dir}/core.py")

    # Write unit test expecting principal * (rate_pct / 100) * years -> $1000 * 5% * 2y = $100.0
    fm.write(
        "tests/test_finance.py",
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        f"from {pkg_dir} import simple_interest\n\n"
        "def test_simple_interest():\n"
        "    assert simple_interest(1000, 5, 2) == 100.0\n",
    )

    # 2. QATester runs real pytest and honestly reports failure (never fabricates pass!)
    qa_reg = AgentRegistry(workspace=str(ws), ai_router=None, file_manager=fm)
    qa_res = qa_reg.run("qa_tester", {
        "payload": {
            "software_type": "python_library",
            "blueprint_analysis": {"software_type": "python_library"},
        }
    })
    assert qa_res.data.get("passed") is False or qa_res.data.get("tests_passed") is False

    # 3. DebuggerAgent inspects failure, applies AI repair, re-runs pytest, and turns suite GREEN!
    fixed_ai_code = (
        "```python\n"
        "def simple_interest(principal: float, rate_pct: float, years: float) -> float:\n"
        "    return round(principal * (rate_pct / 100.0) * years, 2)\n"
        "```\n"
    )
    dbg_reg = AgentRegistry(workspace=str(ws), ai_router=StubLiveRouter(fixed_ai_code), file_manager=fm)
    dbg_res = dbg_reg.run("debugger", {
        "payload": {
            "software_type": "python_library",
            "blueprint_analysis": {"software_type": "python_library"},
            "upstream_handoffs": {"qa_tester": {"data": qa_res.data, "summary": qa_res.summary}},
            "shared_context": {"qa_report": qa_res.data},
        }
    })
    assert dbg_res.ok, dbg_res.error
    assert dbg_res.data.get("final_passed") is True


def test_e2e_rest_api_workflow_skips_ui_and_packages_zip(tmp_path):
    s = Settings(
        telegram_bot_token="t",
        telegram_allowed_user_id=1,
        workspace_root=tmp_path / "ws",
        db_path=tmp_path / "e2e_api.db",
    )
    engine = OrchestratorEngine(s)
    try:
        pid = engine.create_project(
            "BookApi",
            "Build a headless JSON REST API service for managing library books with SQLite. No frontend UI.",
        )
        st = _wait_project(engine, pid)
        assert st["lifecycle"] == "COMPLETED", st
        ran_agents = {t["agent"] for t in st["tasks"]}
        assert "uiux_designer" not in ran_agents
        assert "frontend_developer" not in ran_agents
        assert "api_integrator" in ran_agents

        ws = Path(st["project"]["workspace"])
        assert not (ws / "frontend" / "index.html").exists()

        zip_path = engine.deliverable_zip_path(pid)
        assert Path(zip_path).is_file()
    finally:
        engine.shutdown()


def test_e2e_cli_and_android_and_desktop_workflows(tmp_path):
    s = Settings(
        telegram_bot_token="t",
        telegram_allowed_user_id=1,
        workspace_root=tmp_path / "ws",
        db_path=tmp_path / "e2e_multi.db",
    )
    engine = OrchestratorEngine(s)
    try:
        # CLI Tool
        pid_cli = engine.create_project(
            "LogCli",
            "Build a command-line CLI utility using argparse to manage developer bookmarks.",
        )
        st_cli = _wait_project(engine, pid_cli)
        assert st_cli["lifecycle"] == "COMPLETED", st_cli

        # Android App
        pid_and = engine.create_project(
            "HabitDroid",
            "Build a Kotlin Android mobile app for tracking daily study habits.",
        )
        st_and = _wait_project(engine, pid_and)
        assert st_and["lifecycle"] == "COMPLETED", st_and

        # Desktop GUI App
        pid_dsk = engine.create_project(
            "DeskNotes",
            "Build a Tkinter desktop GUI application for offline scratchpad notes.",
        )
        st_dsk = _wait_project(engine, pid_dsk)
        assert st_dsk["lifecycle"] == "COMPLETED", st_dsk
    finally:
        engine.shutdown()

import tempfile, os
from execution.file_manager import FileManager
from agents.registry import AgentRegistry

def _registry(tmp):
    fm = FileManager(tmp)
    return AgentRegistry(workspace=tmp, db=None, file_manager=fm), fm

def test_requirement_analyst(tmp_path):
    reg, fm = _registry(str(tmp_path))
    r = reg.run("requirement_analyst", {"payload": '{"requirements": "User must login. Must be fast."}', "project_id": "p1"})
    assert r.ok and r.files
    assert fm.exists("docs/requirements.md")

def test_architect_and_backend(tmp_path):
    reg, fm = _registry(str(tmp_path))
    fm.write("docs/requirements.json", '{"project_title": "Todo"}')
    r = reg.run("software_architect", {"payload": "{}", "project_id": "p1"})
    assert r.ok and fm.exists("backend/app.py")
    r2 = reg.run("backend_developer", {"payload": "{}", "project_id": "p1"})
    assert r2.ok

def test_all_agents_runnable(tmp_path):
    reg, fm = _registry(str(tmp_path))
    fm.write("docs/requirements.json", '{"project_title": "X"}')
    fm.write("backend/app.py", "# x\n")
    fm.write("frontend/index.html", "<html></html>")
    fm.write("database/schema.sql", "-- x\n")
    fm.write("docs/test-report.md", "passed")
    for aid in ["uiux_designer", "frontend_developer", "database_engineer", "api_integrator",
                "security_engineer", "qa_tester", "debugger", "code_reviewer", "devops",
                "documentation", "release_manager", "workspace_executor", "chief_orchestrator"]:
        r = reg.run(aid, {"payload": '{"command": "ls -la"}', "project_id": "p1"})
        assert r.ok, f"{aid} failed: {r.error} {r.summary}"

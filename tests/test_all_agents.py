"""All 16 agents must run individually and produce real artifacts."""
import pytest

from agents.registry import AGENT_MODULES, AGENT_NAMES, AgentRegistry
from execution.file_manager import FileManager


@pytest.fixture()
def registry(tmp_path):
    fm = FileManager(str(tmp_path))
    fm.write("requirements.txt", "Build a todo app. Users must add items. Must be fast.")
    return AgentRegistry(workspace=str(tmp_path), ai_router=None, db=None, file_manager=fm), fm


def test_registry_lists_16_agents():
    assert len(AGENT_MODULES) == 16
    assert len(AGENT_NAMES) == 16
    assert set(AGENT_MODULES) == set(AGENT_NAMES)


# Agents that legitimately report failure when their inputs are absent.
MAY_FAIL_WITHOUT_INPUTS = {"qa_tester", "debugger"}


@pytest.mark.parametrize("agent_id", sorted(AGENT_MODULES))
def test_each_agent_runs(agent_id, registry):
    """Every agent must execute without raising and return a real summary.

    qa_tester and debugger are allowed to report a negative verdict when there is
    nothing to test/repair — that is correct behaviour, not a stub.
    """
    reg, fm = registry
    result = reg.run(agent_id, {"payload": '{"command": "ls -la"}', "project_id": "p1"})
    assert result.summary, f"{agent_id} returned no summary"
    if agent_id not in MAY_FAIL_WITHOUT_INPUTS:
        assert result.ok, f"{agent_id} failed: {result.error} | {result.summary}"
    assert result.files or agent_id in {"chief_orchestrator"}, \
        f"{agent_id} produced no artifact"


def test_pipeline_artifacts_are_real(registry):
    """Run the sequential pipeline and assert every expected file exists and is non-trivial."""
    reg, fm = registry
    order = ["requirement_analyst", "software_architect", "uiux_designer", "database_engineer",
             "backend_developer", "frontend_developer", "api_integrator", "security_engineer",
             "qa_tester", "debugger", "code_reviewer", "devops", "documentation",
             "release_manager", "workspace_executor"]
    for agent_id in order:
        res = reg.run(agent_id, {"payload": '{"command": "ls"}', "project_id": "p1"})
        assert res.ok or agent_id == "debugger", f"{agent_id}: {res.error}"

    expected = {
        "docs/requirements.md": 200,
        "docs/architecture.md": 300,
        "backend/app.py": 500,
        "frontend/index.html": 500,
        "database/schema.sql": 50,
        "backend/api_adapter.py": 200,
        "docs/security.md": 100,
        "docs/test-report.md": 20,
        "docs/review.md": 20,
        "scripts/run.sh": 20,
        "README.md": 100,
        "VERSION": 3,
        "docs/workspace-log.md": 20,
    }
    for rel, min_size in expected.items():
        assert fm.exists(rel), f"missing artifact {rel}"
        body = fm.read(rel)
        assert len(body) >= min_size, f"{rel} suspiciously small ({len(body)} bytes)"


def test_agents_do_not_write_outside_workspace(registry, tmp_path):
    """No agent may create files outside its workspace (escape probes included)."""
    reg, fm = registry
    sentinel = tmp_path.parent / "PWNED_BY_AGENT.txt"
    sentinel.unlink(missing_ok=True)
    for agent_id in ("requirement_analyst", "documentation", "workspace_executor", "devops"):
        reg.run(agent_id, {"payload": '{"command": "ls"}', "project_id": "p1"})
    assert not sentinel.exists()
    # Every artifact an agent reported must resolve inside the workspace
    for rel in fm.list_files():
        assert (tmp_path / rel).resolve().is_relative_to(tmp_path.resolve())


def test_unknown_agent_raises(registry):
    reg, _ = registry
    with pytest.raises(KeyError):
        reg.get("not_an_agent")

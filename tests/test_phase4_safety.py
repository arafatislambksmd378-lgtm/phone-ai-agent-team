"""Phase 4 fail-closed and discovery regression tests; no live services."""
import json
from unittest.mock import Mock

from agents.debugger.agent import Agent as Debugger
from agents.qa_tester.agent import Agent as QA
from execution.command_runner import CommandRunner
from execution.file_manager import FileManager
from integrations.github.api import GitHubAPI
from integrations.github.executor import GitHubExecutionService
from memory.database import Database


def test_workspace_commands_never_run_on_host_even_if_approved(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.command_runner.subprocess.run", Mock(side_effect=AssertionError("executed on host")))
    runner = CommandRunner(str(tmp_path))
    result = runner.run("python3 project.py", approved=True)
    assert not result["ok"] and result["blocked"] and "isolated worker" in result["error"]


def test_pytest_is_blocked_in_each_agent_path_even_with_old_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_UNSANDBOXED_PROJECT_TESTS", "1")
    monkeypatch.setattr("subprocess.run", Mock(side_effect=AssertionError("executed on host")))
    qa = QA(workspace=str(tmp_path), file_manager=FileManager(str(tmp_path)))
    debugger = Debugger(workspace=str(tmp_path), file_manager=FileManager(str(tmp_path)))
    assert qa._run_pytest("test.py")[1] == "BLOCKED"
    assert not debugger._run_pytest(["tests"])[0]
    assert not GitHubExecutionService._run_workspace_pytest(tmp_path)["executed"]


def test_github_execution_blocks_before_planning(tmp_path):
    db = Database(tmp_path / "test.db")
    pid = db.create_project("test", str(tmp_path))
    db.bind_repository(pid, "owner/repo", "private", "trunk", "feature")
    engine = Mock()
    result = GitHubExecutionService(db, GitHubAPI("example")).execute_project_on_github(engine, pid, "fix authentication")
    assert result["status"] == 503 and result["blocked"]
    engine.start_project.assert_not_called()


def test_github_pagination_does_not_follow_lookalike_host():
    calls = []
    class Response:
        status = 200
        headers = {"Link": '<https://api.github.com.attacker.invalid/repos?page=2>; rel="next"'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps([{"name": "example"}]).encode()
    def opener(req, timeout):
        calls.append(req.full_url)
        return Response()
    api = GitHubAPI("example", opener=opener)
    assert api.accessible_repositories()["ok"]
    assert len(calls) == 1 and calls[0].startswith("https://api.github.com/")


def test_repository_discovery_filters_response_fields():
    api = GitHubAPI("example")
    api._paged = lambda path: {"ok": True, "data": [{"full_name": "o/r", "private": True,
        "default_branch": "develop", "permissions": {"push": True}, "sensitive_other_field": "not returned"}]}
    result = api.accessible_repositories()
    assert result["repositories"][0]["default_branch"] == "develop"
    assert "sensitive_other_field" not in result["repositories"][0]

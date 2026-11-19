"""Mocked/local Phase 3 workflow coverage; these tests do not contact GitHub or Telegram."""
import base64
import io
import json
import urllib.error
import urllib.request
import zipfile

from integrations.github.api import GitHubAPI
from integrations.github.executor import GitHubExecutionService
from security.validation import safe_extract_zip

from tests.test_phase3_security import TOKEN, request, server  # noqa: F401


def test_local_dashboard_projects_tasks_and_auth(server):
    srv, db = server
    status, created = request(srv, "/api/projects", TOKEN, "POST", json.dumps({"name": "Dashboard"}).encode())
    assert status == 201
    pid = created["id"]
    status, task = request(srv, f"/api/projects/{pid}/tasks", TOKEN, "POST", json.dumps({"title": "Implement feature", "agent_id": "backend_developer", "instruction": "Add a route", "auto_run": False}).encode())
    assert status == 201
    status, history = request(srv, f"/api/projects/{pid}/tasks", TOKEN)
    assert status == 200 and history["tasks"][0]["id"] == task["id"]
    assert db.list_tasks(pid)[0]["title"] == "Implement feature"
    status, agents = request(srv, "/api/agents", TOKEN)
    assert status == 200 and len(agents["agents"]) == 16


def test_zip_traversal_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escape.txt", "bad")
    ok, _, _ = safe_extract_zip(str(archive), str(tmp_path / "out"))
    assert not ok and not (tmp_path / "escape.txt").exists()


def test_create_and_rename_fail_closed_when_destination_unknown():
    api = GitHubAPI("example")
    api.get_file = lambda *args: {"ok": False, "status": 503, "error": "unavailable"}
    api.put_file = lambda *args, **kw: (_ for _ in ()).throw(AssertionError("must not write"))
    assert api.create_file("owner/repo", "a.py", "x", "msg", "trunk")["status"] == 503
    api.read_text = lambda *args: {"ok": True, "sha": "old", "text": "source"}
    assert api.rename_file("owner/repo", "a.py", "b.py", "trunk", sha="old")["status"] == 503
    assert api.rename_file("owner/repo", "a.py", "b.py", "trunk")["status"] == 409


def test_generated_test_execution_is_disabled_without_isolation_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_UNSANDBOXED_PROJECT_TESTS", raising=False)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("assert False")
    result = GitHubExecutionService._run_workspace_pytest(tmp_path)
    assert not result["executed"] and not result["passed"]

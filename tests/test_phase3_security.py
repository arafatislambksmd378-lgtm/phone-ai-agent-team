"""Phase 3 security regression tests; GitHub transport is mocked, not live."""
import concurrent.futures
import json
import threading
import urllib.error
import urllib.request

import pytest

from integrations.api.server import create_api_server
from integrations.github.api import GitHubAPI
from integrations.github.executor import GitHubExecutionService
from memory.database import Database
from security.approval import ApprovalManager

TOKEN = "secure-Token_4x39-Random-value-8yZ2"


@pytest.mark.parametrize("token", ["", "short", "a" * 32])
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_server_refuses_weak_tokens_before_bind(tmp_path, token, host):
    db = Database(tmp_path / "db")
    with pytest.raises(ValueError):
        create_api_server(db, host=host, port=0, api_token=token)


@pytest.fixture
def server(tmp_path):
    db = Database(tmp_path / "db")
    srv = create_api_server(db, port=0, api_token=TOKEN, auth_max_failures=3)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, db
    srv.shutdown()
    srv.server_close()
    thread.join()


def request(srv, path="/api/health", token=None, method="GET", data=None, headers=None):
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_auth_body_limits_and_agent_registry(server):
    srv, _ = server
    assert request(srv)[0] == 401
    assert request(srv, token="wrong")[0] == 401
    status, health = request(srv, token=TOKEN)
    assert status == 200 and health["authenticated"] and health["agents"] == 16
    status, agents = request(srv, "/api/agents", TOKEN)
    assert status == 200 and len(agents["agents"]) == 16
    assert request(srv, "/api/projects", TOKEN, "POST", b"[]")[0] == 400
    assert request(srv, "/api/projects", TOKEN, "POST", b"{}", {"Content-Length": str(3 * 1024 * 1024)})[0] == 413


def test_rate_limit(server):
    srv, _ = server
    for _ in range(3):
        assert request(srv, token="wrong")[0] == 401
    assert request(srv, token=TOKEN)[0] == 429


def test_merge_requires_exact_pr_repo_scope_and_single_use(tmp_path):
    db = Database(tmp_path / "db")
    pid = db.create_project("project", str(tmp_path))
    db.bind_repository(pid, "owner/repo", "private", "develop", "topic")
    calls = []
    api = GitHubAPI("example")
    api.get_pr = lambda full, number: {"ok": True, "data": {"head": {"sha": "a" * 40}}}
    api.merge_pr = lambda full, number, **kwargs: calls.append((full, number)) or {"ok": True, "data": {"merged": True, "sha": "abc"}}
    approvals = ApprovalManager(db)
    service = GitHubExecutionService(db, api, approvals)
    generic = approvals.require(pid, "git_push", "push")
    approvals.approve(generic)
    assert not service.merge(pid, 7, generic)["ok"]
    unscoped = approvals.require(pid, "pr_merge", "merge something")
    approvals.approve(unscoped)
    assert not service.merge(pid, 7, unscoped)["ok"]
    wrong = service.request_merge_approval(pid, 8)["id"]
    approvals.approve(wrong)
    assert not service.merge(pid, 7, wrong)["ok"]
    aid = service.request_merge_approval(pid, 7)["id"]
    approvals.approve(aid)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.merge(pid, 7, aid), range(8)))
    assert sum(bool(r["ok"]) for r in results) == 1
    assert calls == [("owner/repo", 7)]
    old = service.request_merge_approval(pid, 9)["id"]
    approvals.approve(old)
    db.bind_repository(pid, "owner/other", "private", "trunk", "topic")
    assert not service.merge(pid, 9, old)["ok"]


def test_merge_rejects_different_head_without_consuming(tmp_path):
    db = Database(tmp_path / "db-head")
    pid = db.create_project("project", str(tmp_path))
    db.bind_repository(pid, "owner/repo", "private", "trunk", "topic")
    api = GitHubAPI("example")
    api.get_pr = lambda *args: {"ok": True, "data": {"head": {"sha": "a" * 40}}}
    api.merge_pr = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not merge"))
    approvals = ApprovalManager(db)
    service = GitHubExecutionService(db, api, approvals)
    aid = service.request_merge_approval(pid, 1)["id"]
    approvals.approve(aid)
    result = service.merge(pid, 1, aid, sha="b" * 40)
    assert result["status"] == 409
    assert approvals.get(aid)["consumed_at"] is None


def test_repository_default_is_discovered_not_client_override(tmp_path):
    db = Database(tmp_path / "db-branch")
    pid = db.create_project("project", str(tmp_path))
    api = GitHubAPI("example")
    api.permissions = lambda repo: {"ok": True, "default_branch": "develop", "permissions": {"push": True}}
    svc = GitHubExecutionService(db, api)
    result = svc.connect_repository(pid, "owner/repo", default_branch="wrong")
    assert result["ok"]
    assert result["repository"]["default_branch"] == "develop"
    assert result["repository"]["working_branch"] == "develop"

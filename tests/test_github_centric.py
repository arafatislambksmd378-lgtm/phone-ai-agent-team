import json, sqlite3, tempfile, urllib.request
from pathlib import Path
import pytest
from memory.database import Database
from integrations.github.api import GitHubAPI
from integrations.github.executor import GitHubExecutionService
from security.approval import ApprovalManager
class Resp:
    status=200
    def __init__(s,d): s.d=json.dumps(d).encode()
    def read(s): return s.d
    def __enter__(s): return s
    def __exit__(s,*a): pass
def fake_api(calls):
    def opener(req, timeout=20):
        calls.append((req.method, req.full_url, req.data and json.loads(req.data)))
        u=req.full_url
        if "/git/ref/" in u: return Resp({"object":{"sha":"abc"}})
        if "/pulls" in u and req.method=="POST": return Resp({"number":7,"html_url":"https://github.com/o/r/pull/7"})
        return Resp({"sha":"f1"})
    return GitHubAPI("t", opener=opener)
def test_v4_migration_preserves_and_isolates(tmp_path):
    db=Database(tmp_path/"d.db"); pid=db.create_project("A",".")
    db.bind_repository(pid,"o/r"); assert db.assert_repo(pid,"o/r")
    with pytest.raises(PermissionError): db.assert_repo(pid,"evil/x")
def test_no_token_no_fake_success(): assert GitHubAPI("").repo("o/r")["ok"] is False
def test_branch_pr_flow_and_merge_gate(tmp_path):
    db=Database(tmp_path/"d.db"); pid=db.create_project("A","."); db.bind_repository(pid,"o/r")
    calls=[]; svc=GitHubExecutionService(db, fake_api(calls), ApprovalManager(db))
    assert svc.prepare_branch(pid,"task_1")["branch"].startswith("agent/")
    assert svc.open_pull_request(pid,"task_1","agent/x","Title")["data"]["number"]==7
    assert svc.merge(pid,7,"missing")["ok"] is False  # blocked without approval
    assert db.list_pull_requests(pid)[0]["number"]==7

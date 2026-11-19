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
        if u.endswith("/repos/o/r"): return Resp({"default_branch":"develop"})
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

import urllib.error, io, json as _json
from integrations.github.api import GitHubAPI as _API
class _H(dict):
    def items(self): return list(super().items())
class _Resp:
    status=200; headers=_H()
    def __init__(s,d): s.d=_json.dumps(d).encode()
    def read(s): return s.d
    def __enter__(s): return s
    def __exit__(s,*a): pass
def test_pagination_rate_limit_and_conflict(tmp_path):
    calls=[]
    def opener(req, timeout=20):
        calls.append(req.full_url)
        if "branches" in req.full_url and "page=2" not in req.full_url:
            r=_Resp([{"name":"main"}]); r.headers=_H({"Link":'<https://api.github.com/repos/o/r/branches?per_page=100&page=2>; rel="next"'}); return r
        if "branches" in req.full_url: return _Resp([{"name":"dev"}])
        raise urllib.error.HTTPError(req.full_url, 409, "conflict", {}, io.BytesIO(b'{"message":"sha mismatch"}'))
    api=_API("t", opener=opener, sleeper=lambda s: None)
    assert [b["name"] for b in api.branches("o/r")["data"]]==["main","dev"]
    r=api.put_file("o/r","a.py","x","m","main","stale"); assert r["conflict"] is True
def test_api_auth_and_shared_telegram_state(tmp_path):
    from http.server import ThreadingHTTPServer
    from integrations.api.server import make_handler
    from security.approval import ApprovalManager
    import threading, urllib.request
    db=Database(tmp_path/"s.db")
    srv=ThreadingHTTPServer(("127.0.0.1",0), make_handler(db,"test-token-AbC9876-long-enough-12345")); port=srv.server_address[1]
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    # unauthenticated rejected
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/projects"),timeout=5); assert False
    except urllib.error.HTTPError as e: assert e.code==401
    req=urllib.request.Request(f"http://127.0.0.1:{port}/api/projects", data=b'{"name":"FromAndroid"}', method="POST", headers={"Authorization":"Bearer test-token-AbC9876-long-enough-12345","Content-Type":"application/json"})
    pid=_json.loads(urllib.request.urlopen(req,timeout=5).read())["id"]
    # same row visible to Telegram side (shared DB): approval decided via API is pending in DB
    req=urllib.request.Request(f"http://127.0.0.1:{port}/api/projects/{pid}/approvals", data=b'{"action":"git_push","detail":"merge PR"}', method="POST", headers={"Authorization":"Bearer test-token-AbC9876-long-enough-12345","Content-Type":"application/json"})
    aid=_json.loads(urllib.request.urlopen(req,timeout=5).read())["id"]
    assert ApprovalManager(db).pending(pid)[0]["id"]==aid
    srv.shutdown()
def test_agent_execution_verified_on_mocked_github(tmp_path):
    db=Database(tmp_path/"a.db"); pid=db.create_project("AgentProj","."); db.bind_repository(pid,"owner/repo")
    calls=[]
    def opener(req, timeout=20):
        calls.append((req.method, req.full_url))
        u=req.full_url
        if u.endswith('/repos/owner/repo'): return _Resp({'default_branch':'trunk'})
        if "/git/ref/" in u: return _Resp({"object":{"sha":"abc"}})
        if "/compare/" in u: return _Resp({"files":[{"filename":"app.py"}],"ahead_by":1})
        return _Resp({"sha":"new","content":{"sha":"c1"}})
    from integrations.github.executor import GitHubExecutionService
    svc=GitHubExecutionService(db,_API("t",opener=opener,sleeper=lambda s: None),ApprovalManager(db))
    br=svc.prepare_branch(pid,"task_9"); assert br["ok"]
    out=svc.execute_agent_files(pid,"task_9","backend_developer",br["branch"],{"app.py":"print(1)\n"})
    assert out["verified"] is True and out["changed_files"]==["app.py"]
    assert all("owner/repo" in u for _,u in calls)  # isolation: only bound repo touched

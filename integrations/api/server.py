"""Shared JSON API for Android dashboard — same Database/engine as Telegram. Stdlib only."""
from __future__ import annotations
import hmac, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agents.registry import AGENT_NAMES
from integrations.github.diff import summarize_compare
from urllib.parse import urlparse, parse_qs, unquote
def make_handler(db, api_token="", github_service=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, obj): 
            b=json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        def _auth(self): return not api_token or hmac.compare_digest(self.headers.get("Authorization",""), f"Bearer {api_token}")
        def _body(self):
            try: return json.loads(self.rfile.read(int(self.headers.get("Content-Length",0) or 0)) or b"{}")
            except Exception: return {}
        def do_GET(self):
            if not self._auth(): return self._send(401, {"ok":False,"error":"unauthorized"})
            p=self.path.split("?")[0]
            if p=="/api/health": return self._send(200, {"ok":True,"agents":len(AGENT_NAMES)})
            if p=="/api/projects": return self._send(200, {"ok":True,"projects":db.list_projects()})
            if p=="/api/agents": return self._send(200, {"ok":True,"agents":[{"id":a,"name":a} for a in AGENT_NAMES]})
            if p=="/api/approvals": return self._send(200, {"ok":True,"approvals":db.pending_approvals()})
            if p.startswith("/api/projects/"):
                pid=p.split("/")[3]; proj=db.get_project(pid)
                if not proj: return self._send(404, {"ok":False,"error":"project not found"})
                return self._send(200, {"ok":True,"project":proj,"repository":db.get_repository(pid),"tasks":db.list_tasks(pid),"pull_requests":db.list_pull_requests(pid)})
            parts=p.strip("/").split("/")
            if len(parts)>=3 and parts[0]=="api" and parts[1]=="projects" and github_service:
                pid=parts[2]; repo=db.get_repository(pid)
                if not repo: return self._send(404, {"ok":False,"error":"no repository connected"})
                full=repo["repo_full_name"]; api=github_service.api; q=parse_qs(urlparse(self.path).query)
                branch=q.get("branch",[repo.get("working_branch") or repo["default_branch"]])[0]
                if len(parts)==4 and parts[3]=="branches": return self._send(200, api.branches(full))
                if len(parts)==4 and parts[3]=="commits": return self._send(200, api.commits(full, branch))
                if len(parts)==4 and parts[3]=="files": return self._send(200, api.list_dir(full, unquote(q.get("path",[""])[0]), branch))
                if len(parts)==4 and parts[3]=="file":
                    r=api.read_text(full, unquote(q.get("path",[""])[0]), branch); return self._send(200 if r.get("ok") else 502, r)
                if len(parts)==4 and parts[3]=="diff":
                    r=api.compare(full, q.get("base",[repo["default_branch"]])[0], q.get("head",[branch])[0])
                    return self._send(200, {"ok":r.get("ok"), "diff": summarize_compare(r.get("data",{})) if r.get("ok") else None, "error": r.get("error")})
                if len(parts)==4 and parts[3]=="pull-requests": return self._send(200, api.list_prs(full))
                if len(parts)==5 and parts[3]=="pull-requests": return self._send(200, api.get_pr(full, parts[4]))
                if len(parts)==4 and parts[3]=="sync": return self._send(200, {"ok":True,"events":db.query("SELECT * FROM synchronization_events WHERE project_id=? ORDER BY ts DESC LIMIT 100",(pid,))})
            return self._send(404, {"ok":False,"error":"not found"})
        def do_PUT(self): return self._mutate()
        def do_DELETE(self): return self._mutate()
        def _mutate(self):
            if not self._auth(): return self._send(401, {"ok":False,"error":"unauthorized"})
            b=self._body(); parts=self.path.split("?")[0].strip("/").split("/")
            if len(parts)==4 and parts[:2]==["api","projects"] and parts[3]=="file" and github_service:
                pid=parts[2]
                try:
                    if self.command=="DELETE":
                        repo=db.get_repository(pid); r=github_service.api.delete_file(repo["repo_full_name"], b["path"], b.get("message","delete "+b["path"]), b.get("branch", repo["default_branch"]), b["sha"])
                    else:
                        r=github_service.save_user_file(pid, b.get("branch","main"), b["path"], b.get("content",""), b.get("sha"), b.get("message"))
                    db.record_sync(pid,"dashboard","file_"+self.command.lower(), b.get("path","")); return self._send(200 if r.get("ok") else 502, r)
                except Exception as e: return self._send(400, {"ok":False,"error":str(e)})
            return self._send(404, {"ok":False,"error":"not found"})
        def do_POST(self):
            if not self._auth(): return self._send(401, {"ok":False,"error":"unauthorized"})
            b=self._body(); p=self.path
            if p=="/api/projects":
                pid=db.create_project(b.get("name","Untitled"), b.get("workspace",""), {"description":b.get("description","")}); db.record_sync(pid,"dashboard","project_created",b.get("name","")); return self._send(201, {"ok":True,"id":pid})
            if p.startswith("/api/projects/") and p.endswith("/repository"):
                pid=p.split("/")[3]
                try: r=db.bind_repository(pid, b["repo"], b.get("visibility","private"), b.get("default_branch","main"), b.get("working_branch"))
                except Exception as e: return self._send(400, {"ok":False,"error":str(e)})
                return self._send(200, {"ok":True,"repository":r})
            if p.startswith("/api/projects/") and p.endswith("/tasks"):
                pid=p.split("/")[3]
                if not db.get_project(pid): return self._send(404, {"ok":False,"error":"project not found"})
                tid=db.create_task(pid, b.get("agent_id","chief_orchestrator"), b["title"], {"instruction": b.get("instruction",""), "source":"dashboard"})
                db.record_sync(pid,"dashboard","task_created",tid); return self._send(201, {"ok":True,"id":tid})
            if p.startswith("/api/projects/") and p.endswith("/approvals"):
                pid=p.split("/")[3]
                from security.approval import ApprovalManager
                am=ApprovalManager(db)
                try: aid=am.require(pid, b.get("action","git_push"), b.get("detail",""), task_id=b.get("task_id"))
                except Exception as e: return self._send(400, {"ok":False,"error":str(e)})
                db.record_sync(pid,"dashboard","approval_requested",aid); return self._send(201, {"ok":True,"id":aid})
            if p=="/api/approvals/decide":
                from security.approval import ApprovalManager
                am=ApprovalManager(db); ok=am.approve(b["id"], by=b.get("by","dashboard")) if b.get("approved") else am.reject(b["id"], by=b.get("by","dashboard"))
                return self._send(200, {"ok":ok})
            if p.startswith("/api/projects/") and p.endswith("/pull-requests") and github_service:
                pid=p.split("/")[3]; return self._send(200, github_service.open_pull_request(pid, b.get("task_id",""), b["branch"], b["title"]))
            return self._send(404, {"ok":False,"error":"not found"})
    return H
def serve(db, host="0.0.0.0", port=8000, api_token="", github_service=None):
    ThreadingHTTPServer((host,port), make_handler(db,api_token,github_service)).serve_forever()

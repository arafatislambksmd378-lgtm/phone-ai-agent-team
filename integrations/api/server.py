"""Shared JSON API for Android dashboard — same Database/engine as Telegram. Stdlib only."""
from __future__ import annotations
import hmac, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agents.registry import AGENT_NAMES
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
            if p.startswith("/api/projects/") and p.endswith("/pull-requests") and github_service:
                pid=p.split("/")[3]; return self._send(200, github_service.open_pull_request(pid, b.get("task_id",""), b["branch"], b["title"]))
            return self._send(404, {"ok":False,"error":"not found"})
    return H
def serve(db, host="0.0.0.0", port=8000, api_token="", github_service=None):
    ThreadingHTTPServer((host,port), make_handler(db,api_token,github_service)).serve_forever()

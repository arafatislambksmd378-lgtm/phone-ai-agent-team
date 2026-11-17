"""GitHub REST API (stdlib only). Transport injectable for tests; no simulated success."""
from __future__ import annotations
import base64, json, urllib.request, urllib.error
from security.secret_manager import redact
class GitHubAPI:
    def __init__(self, token="", opener=None, base="https://api.github.com"):
        self.token=token or ""; self.base=base; self._opener=opener or urllib.request.urlopen
    def _req(self, method, path, body=None):
        if not self.token: return {"ok": False, "error": "GITHUB_TOKEN not configured"}
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.base+path, data=data, method=method, headers={"Accept":"application/vnd.github+json","Authorization":f"Bearer {self.token}","User-Agent":"phone-ai-agent-team","Content-Type":"application/json"})
        try:
            with self._opener(req, timeout=20) as r: return {"ok": True, "status": r.status, "data": json.loads(r.read().decode() or "{}")}
        except Exception as e: return {"ok": False, "error": redact(str(e))}
    def repo(self, full): return self._req("GET", f"/repos/{full}")
    def create_repo(self, name, private=True, description=""): return self._req("POST","/user/repos",{"name":name,"private":private,"description":description,"auto_init":True})
    def branches(self, full): return self._req("GET", f"/repos/{full}/branches?per_page=100")
    def branch_sha(self, full, branch):
        r=self._req("GET", f"/repos/{full}/git/ref/heads/{branch}")
        return (r.get("data",{}).get("object",{}).get("sha") if r.get("ok") else None), r
    def create_branch(self, full, branch, from_branch="main"):
        sha, r = self.branch_sha(full, from_branch)
        if not sha: return r
        return self._req("POST", f"/repos/{full}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
    def get_file(self, full, path, branch): return self._req("GET", f"/repos/{full}/contents/{path}?ref={branch}")
    def put_file(self, full, path, content, message, branch, sha=None):
        b={"message":message,"content":base64.b64encode(content.encode()).decode(),"branch":branch}
        if sha: b["sha"]=sha
        return self._req("PUT", f"/repos/{full}/contents/{path}", b)
    def compare(self, full, base, head): return self._req("GET", f"/repos/{full}/compare/{base}...{head}")
    def create_pr(self, full, title, head, base, body=""): return self._req("POST", f"/repos/{full}/pulls", {"title":title,"head":head,"base":base,"body":body})
    def list_prs(self, full): return self._req("GET", f"/repos/{full}/pulls?state=all&per_page=50")
    def merge_pr(self, full, number): return self._req("PUT", f"/repos/{full}/pulls/{number}/merge", {"merge_method":"merge"})

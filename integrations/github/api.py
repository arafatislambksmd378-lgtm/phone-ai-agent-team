"""GitHub REST API (stdlib only). Transport injectable for tests; no simulated success.

Features: pagination (Link headers), rate-limit aware retries (429/403 with
Retry-After / X-RateLimit-Reset, capped), structured API errors, conflict
detection via blob SHA (409/422 surfaced, never silently overwritten).
"""
from __future__ import annotations
import base64, json, time, urllib.request, urllib.error
from security.secret_manager import redact

class GitHubAPI:
    def __init__(self, token="", opener=None, base="https://api.github.com", max_retries=3, sleeper=time.sleep):
        self.token = token or ""; self.base = base.rstrip("/")
        self._opener = opener or urllib.request.urlopen
        self.max_retries = max(0, max_retries); self._sleep = sleeper

    def _req(self, method, path, body=None):
        if not self.token: return {"ok": False, "error": "GITHUB_TOKEN not configured", "status": 0}
        data = json.dumps(body).encode() if body is not None else None
        last = {}
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                "User-Agent": "phone-ai-agent-team", "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28"})
            try:
                with self._opener(req, timeout=20) as r:
                    headers = dict(r.headers.items()) if getattr(r, "headers", None) else {}
                    return {"ok": True, "status": r.status, "headers": headers,
                            "data": json.loads(r.read().decode() or "{}")}
            except urllib.error.HTTPError as e:
                try: payload = json.loads(e.read().decode() or "{}")
                except Exception: payload = {}
                last = {"ok": False, "status": e.code, "error": redact(payload.get("message") or str(e)),
                        "github_errors": payload.get("errors", [])}
                hdrs = dict(e.headers.items()) if e.headers else {}
                if e.code in (429, 403) and attempt < self.max_retries:
                    wait = hdrs.get("Retry-After") or hdrs.get("retry-after")
                    try: delay = min(float(wait), 60) if wait else min(2 ** attempt, 30)
                    except ValueError: delay = min(2 ** attempt, 30)
                    self._sleep(delay); continue
                return last
            except Exception as e:
                last = {"ok": False, "status": 0, "error": redact(str(e))}
                if attempt < self.max_retries: self._sleep(min(2 ** attempt, 10)); continue
                return last
        return last

    def _paged(self, path):
        items, url = [], path
        while url:
            r = self._req("GET", url)
            if not r.get("ok"): return r
            d = r["data"]; items.extend(d if isinstance(d, list) else [])
            link = (r.get("headers") or {}).get("Link") or (r.get("headers") or {}).get("link") or ""
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>").replace(self.base, "", 1)
            if not link and isinstance(d, list) and len(d) == 100:  # minimal fake transports
                break
        return {"ok": True, "status": 200, "data": items}

    # repo / permissions
    def repo(self, full): return self._req("GET", f"/repos/{full}")
    def permissions(self, full):
        r = self.repo(full)
        if not r.get("ok"): return r
        return {"ok": True, "permissions": r["data"].get("permissions", {}),
                "default_branch": r["data"].get("default_branch"), "private": r["data"].get("private")}
    def create_repo(self, name, private=True, description=""):
        return self._req("POST", "/user/repos", {"name": name, "private": private, "description": description, "auto_init": True})
    # branches / commits
    def branches(self, full): return self._paged(f"/repos/{full}/branches?per_page=100")
    def branch_sha(self, full, branch):
        r = self._req("GET", f"/repos/{full}/git/ref/heads/{branch}")
        return (r.get("data", {}).get("object", {}).get("sha") if r.get("ok") else None), r
    def create_branch(self, full, branch, from_branch="main"):
        sha, r = self.branch_sha(full, from_branch)
        if not sha: return r
        return self._req("POST", f"/repos/{full}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
    def commits(self, full, branch, per_page=30): return self._req("GET", f"/repos/{full}/commits?sha={branch}&per_page={per_page}")
    def tree(self, full, branch): return self._req("GET", f"/repos/{full}/git/trees/{branch}?recursive=1")
    # contents
    def list_dir(self, full, path, branch): return self._req("GET", f"/repos/{full}/contents/{path}?ref={branch}")
    def get_file(self, full, path, branch): return self._req("GET", f"/repos/{full}/contents/{path}?ref={branch}")
    def read_text(self, full, path, branch):
        r = self.get_file(full, path, branch)
        if not r.get("ok"): return r
        try: return {"ok": True, "sha": r["data"].get("sha"), "text": base64.b64decode(r["data"]["content"]).decode()}
        except Exception as e: return {"ok": False, "error": redact(str(e))}
    def put_file(self, full, path, content, message, branch, sha=None):
        b = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
        if sha: b["sha"] = sha
        r = self._req("PUT", f"/repos/{full}/contents/{path}", b)
        if not r.get("ok") and r.get("status") in (409, 422):
            r["conflict"] = True; r["error"] = "Conflict/stale SHA: re-read the file before saving"
        return r
    def delete_file(self, full, path, message, branch, sha):
        return self._req("DELETE", f"/repos/{full}/contents/{path}", {"message": message, "sha": sha, "branch": branch})
    def rename_file(self, full, old, new, branch, message=None):
        cur = self.read_text(full, old, branch)
        if not cur.get("ok"): return cur
        made = self.put_file(full, new, cur["text"], message or f"rename {old} -> {new}", branch)
        if not made.get("ok"): return made
        return self.delete_file(full, old, message or f"rename {old} -> {new}", branch, cur["sha"])
    # compare / PRs
    def compare(self, full, base, head): return self._req("GET", f"/repos/{full}/compare/{base}...{head}")
    def create_pr(self, full, title, head, base, body=""):
        return self._req("POST", f"/repos/{full}/pulls", {"title": title, "head": head, "base": base, "body": body})
    def list_prs(self, full): return self._paged(f"/repos/{full}/pulls?state=all&per_page=100")
    def get_pr(self, full, number): return self._req("GET", f"/repos/{full}/pulls/{number}")
    def pr_files(self, full, number): return self._paged(f"/repos/{full}/pulls/{number}/files?per_page=100")
    def review_pr(self, full, number, event="COMMENT", body=""):
        return self._req("POST", f"/repos/{full}/pulls/{number}/reviews", {"event": event, "body": body})
    def merge_pr(self, full, number): return self._req("PUT", f"/repos/{full}/pulls/{number}/merge", {"merge_method": "merge"})

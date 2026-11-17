"""GitHub REST API (stdlib only). Transport injectable for tests; no simulated success.

Features:
  * Link-header pagination
  * Rate-limit aware bounded retries (429/403 with Retry-After / exponential backoff)
  * Strict repository/branch/path validation (no traversal, no hardcoded 'main')
  * Stale-SHA conflict detection on update, create, rename, and delete (never overwrites silently)
  * Complete Pull Request lifecycle (list, detail, files, reviews, comments, create, review, merge)
  * Secret scanning on file writes and secret redaction on all error payloads
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from security.code_scanner import detect_high_confidence_secrets
from security.secret_manager import redact
from security.validation import validate_branch_name, validate_repo_full_name, validate_repo_path

MAX_EDITABLE_FILE_BYTES = 2 * 1024 * 1024  # 2 MB inline editor cap
VALID_PR_STATES = {"open", "closed", "all"}
VALID_REVIEW_EVENTS = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
VALID_MERGE_METHODS = {"merge", "squash", "rebase"}


def _encode_repo(full: str) -> tuple[bool, str]:
    ok, err = validate_repo_full_name(full)
    if not ok:
        return False, err
    owner, name = full.strip().split("/", 1)
    return True, f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"


def _encode_path(path: str, *, allow_empty: bool = False) -> tuple[bool, str]:
    ok, res = validate_repo_path(path, allow_empty=allow_empty)
    if not ok:
        return False, res
    if not res:
        return True, ""
    return True, "/".join(urllib.parse.quote(seg, safe="") for seg in res.split("/"))


def _encode_ref_segments(branch: str) -> tuple[bool, str]:
    ok, err = validate_branch_name(branch)
    if not ok:
        return False, err
    return True, "/".join(urllib.parse.quote(seg, safe="") for seg in branch.strip().split("/"))


def _encode_query_val(val: str) -> str:
    return urllib.parse.quote(str(val), safe="")


def _parse_pr_number(number) -> tuple[bool, int | str]:
    try:
        n = int(str(number).strip())
    except (TypeError, ValueError):
        return False, f"Invalid pull request number: {number!r}"
    if n <= 0:
        return False, f"Pull request number must be positive: {number!r}"
    return True, n


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent a GitHub response from forwarding backend credentials to another host."""
    def __init__(self, base: str):
        super().__init__()
        self.origin = urllib.parse.urlsplit(base)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dest = urllib.parse.urlsplit(newurl)
        if (dest.scheme != self.origin.scheme or dest.netloc != self.origin.netloc
                or dest.username or dest.password):
            raise urllib.error.HTTPError(req.full_url, 403, "Cross-origin redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubAPI:
    def __init__(self, token: str = "", opener=None, base: str = "https://api.github.com",
                 max_retries: int = 3, sleeper=time.sleep):
        self.token = (token or "").strip()
        self.base = base.rstrip("/")
        self._opener = opener or urllib.request.build_opener(_SameOriginRedirect(self.base)).open
        self.max_retries = max(0, int(max_retries))
        self._sleep = sleeper

    def _req(self, method: str, path: str, body=None) -> dict:
        if not self.token:
            return {"ok": False, "error": "GITHUB_TOKEN not configured", "status": 0}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last: dict = {}
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.base + path,
                data=data,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "phone-ai-agent-team",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with self._opener(req, timeout=25) as r:
                    headers = dict(r.headers.items()) if getattr(r, "headers", None) else {}
                    raw = r.read().decode("utf-8", errors="replace")
                    parsed = json.loads(raw) if raw.strip() else {}
                    return {"ok": True, "status": getattr(r, "status", 200), "headers": headers, "data": parsed}
            except urllib.error.HTTPError as e:
                try:
                    payload = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
                except Exception:
                    payload = {}
                msg = payload.get("message") if isinstance(payload, dict) else ""
                gh_errs = payload.get("errors", []) if isinstance(payload, dict) else []
                last = {
                    "ok": False,
                    "status": e.code,
                    "error": redact(msg or str(e)),
                    "github_errors": gh_errs,
                }
                hdrs = dict(e.headers.items()) if e.headers else {}
                low_hdrs = {k.lower(): v for k, v in hdrs.items()}
                is_rate_limit = (
                    e.code == 429
                    or (
                        e.code == 403
                        and (
                            "retry-after" in low_hdrs
                            or low_hdrs.get("x-ratelimit-remaining") == "0"
                            or "rate limit" in (msg or "").lower()
                        )
                    )
                )
                if is_rate_limit and attempt < self.max_retries:
                    wait = low_hdrs.get("retry-after")
                    try:
                        delay = min(float(wait), 60.0) if wait else min(2 ** attempt, 30.0)
                    except ValueError:
                        delay = min(2 ** attempt, 30.0)
                    self._sleep(delay)
                    continue
                return last
            except Exception as e:
                last = {"ok": False, "status": 0, "error": redact(str(e))}
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 10))
                    continue
                return last
        return last

    def _paged(self, path: str, max_pages: int = 20) -> dict:
        items: list = []
        url: str | None = path
        pages = 0
        while url and pages < max_pages:
            pages += 1
            r = self._req("GET", url)
            if not r.get("ok"):
                return r
            d = r["data"]
            items.extend(d if isinstance(d, list) else [])
            link = (r.get("headers") or {}).get("Link") or (r.get("headers") or {}).get("link") or ""
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part.split(";")[0].strip().strip("<>")
                    base_url = urllib.parse.urlsplit(self.base)
                    next_url = urllib.parse.urlsplit(nxt)
                    # Never forward the bearer token to a lookalike host or userinfo URL.
                    if (next_url.scheme == base_url.scheme and next_url.netloc == base_url.netloc
                            and not next_url.username and not next_url.password):
                        url = urllib.parse.urlunsplit(("", "", next_url.path, next_url.query, ""))
                    break
        return {"ok": True, "status": 200, "data": items}

    # ---------- repository & permissions ----------
    def authenticated_user(self) -> dict:
        r = self._req("GET", "/user")
        if not r.get("ok"):
            return r
        d = r.get("data") or {}
        return {"ok": True, "status": 200, "user": {"login": d.get("login") or "",
                "name": d.get("name") or "", "html_url": d.get("html_url") or ""}}

    def accessible_repositories(self) -> dict:
        r = self._paged("/user/repos?per_page=100&sort=updated&affiliation=owner%2Ccollaborator%2Corganization_member")
        if not r.get("ok"):
            return r
        repos = []
        for item in r.get("data") or []:
            if not isinstance(item, dict):
                continue
            repos.append({"full_name": item.get("full_name") or "", "private": bool(item.get("private")),
                          "default_branch": item.get("default_branch") or "",
                          "permissions": item.get("permissions") or {}, "html_url": item.get("html_url") or ""})
        return {"ok": True, "status": 200, "repositories": repos}

    def repo(self, full: str) -> dict:
        ok, enc = _encode_repo(full)
        if not ok:
            return {"ok": False, "status": 400, "error": enc}
        return self._req("GET", f"/repos/{enc}")

    def default_branch(self, full: str) -> tuple[str | None, dict]:
        """Fetch the repository's configured default branch from GitHub (never assumes 'main')."""
        r = self.repo(full)
        if not r.get("ok"):
            return None, r
        branch = ((r.get("data") or {}).get("default_branch") or "").strip()
        if not branch:
            return None, {"ok": False, "status": 502, "error": "Repository did not report a default_branch"}
        return branch, r

    def permissions(self, full: str) -> dict:
        r = self.repo(full)
        if not r.get("ok"):
            return r
        d = r.get("data") or {}
        return {
            "ok": True,
            "status": r.get("status", 200),
            "full_name": d.get("full_name") or full,
            "permissions": d.get("permissions") or {},
            "default_branch": d.get("default_branch") or "",
            "private": bool(d.get("private", True)),
            "visibility": d.get("visibility") or ("private" if d.get("private", True) else "public"),
        }

    def create_repo(self, name: str, private: bool = True, description: str = "") -> dict:
        return self._req(
            "POST", "/user/repos",
            {"name": name, "private": bool(private), "description": description, "auto_init": True},
        )

    # ---------- branches & commits ----------
    def branches(self, full: str) -> dict:
        ok, enc = _encode_repo(full)
        if not ok:
            return {"ok": False, "status": 400, "error": enc}
        return self._paged(f"/repos/{enc}/branches?per_page=100")

    def branch_sha(self, full: str, branch: str) -> tuple[str | None, dict]:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return None, {"ok": False, "status": 400, "error": enc_r}
        ok_b, enc_b = _encode_ref_segments(branch)
        if not ok_b:
            return None, {"ok": False, "status": 400, "error": enc_b}
        r = self._req("GET", f"/repos/{enc_r}/git/ref/heads/{enc_b}")
        if not r.get("ok"):
            return None, r
        data = r.get("data") or {}
        sha = (data.get("object") or {}).get("sha") if isinstance(data, dict) else None
        return sha, r

    def create_branch(self, full: str, branch: str, from_branch: str | None = None) -> dict:
        """Create a branch from `from_branch`, or dynamically from the repo's configured default branch."""
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_b, err_b = validate_branch_name(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        base = (from_branch or "").strip()
        if not base:
            resolved, meta_res = self.default_branch(full)
            if not resolved:
                return meta_res
            base = resolved
        ok_base, err_base = validate_branch_name(base)
        if not ok_base:
            return {"ok": False, "status": 400, "error": err_base}
        sha, r = self.branch_sha(full, base)
        if not sha:
            return r
        created = self._req(
            "POST", f"/repos/{enc_r}/git/refs",
            {"ref": f"refs/heads/{branch.strip()}", "sha": sha},
        )
        if not created.get("ok") and created.get("status") in (409, 422):
            created["conflict"] = True
            created["error"] = f"Branch {branch.strip()!r} already exists or ref conflicted: {created.get('error', '')}"
        elif created.get("ok"):
            created["branch"] = branch.strip()
            created["from_branch"] = base
            created["sha"] = sha
        return created

    def commits(self, full: str, branch: str | None = None, per_page: int = 30) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        limit = max(1, min(100, int(per_page or 30)))
        target = (branch or "").strip()
        if target:
            ok_b, err_b = validate_branch_name(target)
            if not ok_b:
                return {"ok": False, "status": 400, "error": err_b}
            return self._req("GET", f"/repos/{enc_r}/commits?sha={_encode_query_val(target)}&per_page={limit}")
        return self._req("GET", f"/repos/{enc_r}/commits?per_page={limit}")

    def tree(self, full: str, branch: str) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_b, enc_b = _encode_ref_segments(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": enc_b}
        return self._req("GET", f"/repos/{enc_r}/git/trees/{enc_b}?recursive=1")

    # ---------- repository file browser & safe edits ----------
    def list_dir(self, full: str, path: str = "", branch: str | None = None) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_p, clean_path = validate_repo_path(path or "", allow_empty=True)
        if not ok_p:
            return {"ok": False, "status": 400, "error": clean_path}
        _, enc_p = _encode_path(clean_path, allow_empty=True)
        q = ""
        if branch and branch.strip():
            ok_b, err_b = validate_branch_name(branch)
            if not ok_b:
                return {"ok": False, "status": 400, "error": err_b}
            q = f"?ref={_encode_query_val(branch.strip())}"
        endpoint = f"/repos/{enc_r}/contents/{enc_p}{q}" if enc_p else f"/repos/{enc_r}/contents{q}"
        r = self._req("GET", endpoint)
        if not r.get("ok"):
            return r
        raw = r.get("data")
        items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) and raw else [])
        normalized = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            etype = entry.get("type") or "file"
            normalized.append({
                "name": entry.get("name") or (entry.get("path") or "").rsplit("/", 1)[-1],
                "path": entry.get("path") or "",
                "type": "dir" if etype == "dir" else etype,
                "size": int(entry.get("size") or 0),
                "sha": entry.get("sha") or "",
            })
        normalized.sort(key=lambda it: (0 if it["type"] == "dir" else 1, it["name"].lower()))
        parent = clean_path.rsplit("/", 1)[0] if "/" in clean_path else ""
        return {
            "ok": True,
            "status": r.get("status", 200),
            "path": clean_path,
            "parent_path": parent if clean_path else None,
            "branch": (branch or "").strip(),
            "data": normalized,
        }

    def get_file(self, full: str, path: str, branch: str | None = None) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_p, enc_p = _encode_path(path, allow_empty=False)
        if not ok_p:
            return {"ok": False, "status": 400, "error": enc_p}
        q = ""
        if branch and branch.strip():
            ok_b, err_b = validate_branch_name(branch)
            if not ok_b:
                return {"ok": False, "status": 400, "error": err_b}
            q = f"?ref={_encode_query_val(branch.strip())}"
        return self._req("GET", f"/repos/{enc_r}/contents/{enc_p}{q}")

    def read_text(self, full: str, path: str, branch: str | None = None) -> dict:
        r = self.get_file(full, path, branch)
        if not r.get("ok"):
            return r
        d = r.get("data")
        if isinstance(d, list):
            return {"ok": False, "status": 400, "error": "Selected path is a directory, not a file"}
        if not isinstance(d, dict):
            return {"ok": False, "status": 502, "error": "Unexpected response payload from GitHub"}
        if d.get("type") not in (None, "file"):
            return {"ok": False, "status": 400, "error": f"Path is a {d.get('type')}, not a regular file"}
        size = int(d.get("size") or 0)
        if size > MAX_EDITABLE_FILE_BYTES:
            return {
                "ok": False,
                "status": 413,
                "error": f"File too large for inline editor ({size} bytes; max {MAX_EDITABLE_FILE_BYTES})",
            }
        raw_content = d.get("content")
        if raw_content is None:
            return {"ok": False, "status": 422, "error": "GitHub returned no inline content (binary or oversized file)"}
        try:
            decoded_bytes = base64.b64decode(raw_content)
        except Exception as e:
            return {"ok": False, "status": 502, "error": redact(str(e))}
        if b"\x00" in decoded_bytes[:8192]:
            return {
                "ok": False,
                "status": 415,
                "binary": True,
                "sha": d.get("sha") or "",
                "size": size,
                "error": "Binary file cannot be edited as UTF-8 text",
            }
        try:
            text = decoded_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "ok": False,
                "status": 415,
                "binary": True,
                "sha": d.get("sha") or "",
                "size": size,
                "error": "File is not valid UTF-8 text",
            }
        clean_path = d.get("path") or path.strip()
        return {
            "ok": True,
            "status": 200,
            "path": clean_path,
            "name": d.get("name") or clean_path.rsplit("/", 1)[-1],
            "branch": (branch or "").strip(),
            "sha": d.get("sha") or "",
            "size": size,
            "text": text,
        }

    def put_file(self, full: str, path: str, content: str, message: str, branch: str,
                 sha: str | None = None, *, scan_secrets: bool = True) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_p, clean_path = validate_repo_path(path, allow_empty=False)
        if not ok_p:
            return {"ok": False, "status": 400, "error": clean_path}
        _, enc_p = _encode_path(clean_path, allow_empty=False)
        ok_b, err_b = validate_branch_name(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        if scan_secrets:
            leaks = detect_high_confidence_secrets(content or "")
            if leaks:
                return {
                    "ok": False,
                    "status": 422,
                    "blocked_by_secrets": True,
                    "findings": leaks,
                    "error": f"Refused to commit file containing detected secret(s): {', '.join(leaks)}",
                }
        commit_msg = (message or f"Update {clean_path}").strip()[:300]
        body: dict = {
            "message": commit_msg,
            "content": base64.b64encode((content or "").encode("utf-8")).decode("ascii"),
            "branch": branch.strip(),
        }
        if sha:
            body["sha"] = sha.strip()
        r = self._req("PUT", f"/repos/{enc_r}/contents/{enc_p}", body)
        if not r.get("ok") and r.get("status") in (409, 422):
            r["conflict"] = True
            r["error"] = "Conflict/stale SHA: re-read the file before saving"
            return r
        if r.get("ok"):
            d = r.get("data") or {}
            new_sha = ((d.get("content") or {}).get("sha") if isinstance(d.get("content"), dict) else None) or d.get("sha") or ""
            commit_sha = ((d.get("commit") or {}).get("sha") if isinstance(d.get("commit"), dict) else None) or ""
            r["path"] = clean_path
            r["branch"] = branch.strip()
            r["sha"] = new_sha
            r["commit_sha"] = commit_sha
        return r

    def create_file(self, full: str, path: str, content: str, message: str, branch: str) -> dict:
        """Create a brand-new file; fails with conflict if the path already exists on `branch`."""
        ok_p, clean_path = validate_repo_path(path, allow_empty=False)
        if not ok_p:
            return {"ok": False, "status": 400, "error": clean_path}
        ok_b, err_b = validate_branch_name(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        existing = self.get_file(full, clean_path, branch)
        if existing.get("ok"):
            cur_sha = (existing.get("data") or {}).get("sha", "") if isinstance(existing.get("data"), dict) else ""
            return {
                "ok": False,
                "status": 409,
                "conflict": True,
                "current_sha": cur_sha,
                "error": f"File {clean_path!r} already exists on branch {branch!r}; open it to edit instead of overwriting",
            }
        if existing.get("status") != 404:
            return existing  # Unknown state is not proof that the destination is absent.
        return self.put_file(full, clean_path, content, message or f"Create {clean_path}", branch, sha=None)

    def update_file(self, full: str, path: str, content: str, message: str, branch: str, sha: str) -> dict:
        """Update an existing file with mandatory blob SHA verification (never overwrites silently)."""
        clean_sha = (sha or "").strip()
        if not clean_sha:
            return {
                "ok": False,
                "status": 409,
                "conflict": True,
                "error": "File SHA is required to save edits; reload the file first to prevent overwriting remote changes",
            }
        existing = self.get_file(full, path, branch)
        if existing.get("ok") and isinstance(existing.get("data"), dict):
            remote_sha = (existing["data"].get("sha") or "").strip()
            if remote_sha and remote_sha != clean_sha:
                return {
                    "ok": False,
                    "status": 409,
                    "conflict": True,
                    "current_sha": remote_sha,
                    "error": (
                        f"Stale SHA conflict on {path}: your editor has {clean_sha[:7]} "
                        f"but remote branch {branch} is at {remote_sha[:7]}. Reload before saving."
                    ),
                }
        res = self.put_file(full, path, content, message or f"Update {path}", branch, sha=clean_sha)
        if res.get("conflict") and "current_sha" not in res:
            latest = self.get_file(full, path, branch)
            if latest.get("ok") and isinstance(latest.get("data"), dict):
                res["current_sha"] = latest["data"].get("sha") or ""
        return res

    def delete_file(self, full: str, path: str, message: str, branch: str, sha: str) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_p, clean_path = validate_repo_path(path, allow_empty=False)
        if not ok_p:
            return {"ok": False, "status": 400, "error": clean_path}
        _, enc_p = _encode_path(clean_path, allow_empty=False)
        ok_b, err_b = validate_branch_name(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}
        clean_sha = (sha or "").strip()
        if not clean_sha:
            return {"ok": False, "status": 400, "error": "File SHA is required for deletion"}
        r = self._req(
            "DELETE",
            f"/repos/{enc_r}/contents/{enc_p}",
            {"message": (message or f"Delete {clean_path}").strip()[:300], "sha": clean_sha, "branch": branch.strip()},
        )
        if not r.get("ok") and r.get("status") in (409, 422):
            r["conflict"] = True
            r["error"] = "Conflict/stale SHA: file changed on remote before deletion — refresh and retry"
        elif r.get("ok"):
            d = r.get("data") or {}
            r["path"] = clean_path
            r["branch"] = branch.strip()
            r["commit_sha"] = ((d.get("commit") or {}).get("sha") if isinstance(d.get("commit"), dict) else None) or ""
        return r

    def rename_file(self, full: str, old: str, new: str, branch: str,
                    sha: str | None = None, message: str | None = None) -> dict:
        """Safely rename/move a file: verifies source SHA and refuses to overwrite an existing destination."""
        ok_old, clean_old = validate_repo_path(old, allow_empty=False)
        if not ok_old:
            return {"ok": False, "status": 400, "error": clean_old}
        ok_new, clean_new = validate_repo_path(new, allow_empty=False)
        if not ok_new:
            return {"ok": False, "status": 400, "error": clean_new}
        if clean_old == clean_new:
            return {"ok": False, "status": 400, "error": "New path must differ from current path"}
        ok_b, err_b = validate_branch_name(branch)
        if not ok_b:
            return {"ok": False, "status": 400, "error": err_b}

        if not sha or not sha.strip():
            return {"ok": False, "status": 409, "conflict": True,
                    "error": "File SHA is required for rename; reload before retrying"}
        cur = self.read_text(full, clean_old, branch)
        if not cur.get("ok"):
            return cur
        remote_sha = cur.get("sha") or ""
        if remote_sha and sha.strip() != remote_sha:
            return {
                "ok": False,
                "status": 409,
                "conflict": True,
                "current_sha": remote_sha,
                "error": f"Stale SHA conflict: {clean_old} was modified remotely ({remote_sha[:7]}). Refresh before renaming.",
            }
        dest = self.get_file(full, clean_new, branch)
        if dest.get("ok"):
            return {
                "ok": False,
                "status": 409,
                "conflict": True,
                "error": f"Cannot rename: destination {clean_new!r} already exists on branch {branch!r}",
            }
        if dest.get("status") != 404:
            return dest  # Do not write when destination existence could not be determined.
        commit_msg = (message or f"Rename {clean_old} -> {clean_new}").strip()[:300]
        made = self.put_file(full, clean_new, cur["text"], commit_msg, branch, sha=None)
        if not made.get("ok"):
            return made
        deleted = self.delete_file(full, clean_old, commit_msg, branch, remote_sha)
        if not deleted.get("ok"):
            return {
                "ok": False,
                "status": deleted.get("status", 502),
                "conflict": bool(deleted.get("conflict")),
                "partial": True,
                "new_path": clean_new,
                "new_sha": made.get("sha", ""),
                "error": f"Created {clean_new} but failed to remove {clean_old}: {deleted.get('error')}",
            }
        return {
            "ok": True,
            "status": 200,
            "old_path": clean_old,
            "new_path": clean_new,
            "branch": branch.strip(),
            "sha": made.get("sha") or "",
            "commit_sha": deleted.get("commit_sha") or made.get("commit_sha") or "",
        }

    # ---------- branch comparison & Pull Requests ----------
    def compare(self, full: str, base: str, head: str) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_base, enc_base = _encode_ref_segments(base)
        if not ok_base:
            return {"ok": False, "status": 400, "error": f"Invalid base branch: {enc_base}"}
        ok_head, enc_head = _encode_ref_segments(head)
        if not ok_head:
            return {"ok": False, "status": 400, "error": f"Invalid head branch: {enc_head}"}
        return self._req("GET", f"/repos/{enc_r}/compare/{enc_base}...{enc_head}")

    def create_pr(self, full: str, title: str, head: str, base: str | None = None,
                  body: str = "", draft: bool = False) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        clean_title = (title or "").strip()
        if not clean_title:
            return {"ok": False, "status": 400, "error": "Pull Request title is required"}
        ok_h, err_h = validate_branch_name(head)
        if not ok_h:
            return {"ok": False, "status": 400, "error": f"Invalid head branch: {err_h}"}
        resolved_base = (base or "").strip()
        if not resolved_base:
            db_branch, meta_res = self.default_branch(full)
            if not db_branch:
                return meta_res
            resolved_base = db_branch
        ok_b, err_b = validate_branch_name(resolved_base)
        if not ok_b:
            return {"ok": False, "status": 400, "error": f"Invalid base branch: {err_b}"}
        if head.strip() == resolved_base:
            return {"ok": False, "status": 400, "error": f"Head branch and base branch cannot both be {resolved_base!r}"}
        payload = {
            "title": clean_title[:256],
            "head": head.strip(),
            "base": resolved_base,
            "body": (body or "")[:60_000],
        }
        if draft:
            payload["draft"] = True
        return self._req("POST", f"/repos/{enc_r}/pulls", payload)

    def list_prs(self, full: str, state: str = "all") -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        st = (state or "all").strip().lower()
        if st not in VALID_PR_STATES:
            return {"ok": False, "status": 400, "error": f"Invalid PR state filter: {state!r}"}
        return self._paged(f"/repos/{enc_r}/pulls?state={st}&per_page=100")

    def get_pr(self, full: str, number) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        return self._req("GET", f"/repos/{enc_r}/pulls/{n}")

    def pr_files(self, full: str, number) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        return self._paged(f"/repos/{enc_r}/pulls/{n}/files?per_page=100")

    def pr_reviews(self, full: str, number) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        return self._paged(f"/repos/{enc_r}/pulls/{n}/reviews?per_page=100")

    def pr_comments(self, full: str, number) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        issue_c = self._paged(f"/repos/{enc_r}/issues/{n}/comments?per_page=100")
        if not issue_c.get("ok"):
            return issue_c
        review_c = self._paged(f"/repos/{enc_r}/pulls/{n}/comments?per_page=100")
        inline = review_c.get("data", []) if review_c.get("ok") else []
        merged: list[dict] = []
        for c in issue_c.get("data") or []:
            merged.append({
                "id": c.get("id"),
                "kind": "comment",
                "user": (c.get("user") or {}).get("login") or "",
                "body": c.get("body") or "",
                "path": None,
                "line": None,
                "created_at": c.get("created_at") or "",
                "html_url": c.get("html_url") or "",
            })
        for c in inline:
            merged.append({
                "id": c.get("id"),
                "kind": "inline",
                "user": (c.get("user") or {}).get("login") or "",
                "body": c.get("body") or "",
                "path": c.get("path") or "",
                "line": c.get("line") or c.get("original_line"),
                "created_at": c.get("created_at") or "",
                "html_url": c.get("html_url") or "",
            })
        merged.sort(key=lambda item: item.get("created_at") or "")
        return {"ok": True, "status": 200, "data": merged}

    def add_pr_comment(self, full: str, number, body: str, *,
                       path: str | None = None, line: int | None = None,
                       commit_id: str | None = None, side: str = "RIGHT") -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        clean_body = (body or "").strip()
        if not clean_body:
            return {"ok": False, "status": 400, "error": "Comment body is required"}
        if path and line is not None:
            ok_p, clean_path = validate_repo_path(path, allow_empty=False)
            if not ok_p:
                return {"ok": False, "status": 400, "error": clean_path}
            try:
                line_num = int(line)
            except (TypeError, ValueError):
                return {"ok": False, "status": 400, "error": "Line number must be an integer"}
            if line_num <= 0:
                return {"ok": False, "status": 400, "error": "Line number must be positive"}
            cid = (commit_id or "").strip()
            if not cid:
                pr = self.get_pr(full, n)
                if not pr.get("ok"):
                    return pr
                cid = ((pr.get("data") or {}).get("head") or {}).get("sha") or ""
                if not cid:
                    return {"ok": False, "status": 502, "error": "Could not determine PR head commit SHA for inline comment"}
            return self._req(
                "POST",
                f"/repos/{enc_r}/pulls/{n}/comments",
                {
                    "body": clean_body[:20_000],
                    "commit_id": cid,
                    "path": clean_path,
                    "line": line_num,
                    "side": "LEFT" if str(side).upper() == "LEFT" else "RIGHT",
                },
            )
        return self._req("POST", f"/repos/{enc_r}/issues/{n}/comments", {"body": clean_body[:20_000]})

    def review_pr(self, full: str, number, event: str = "COMMENT", body: str = "",
                  comments: list[dict] | None = None) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        ev = (event or "COMMENT").strip().upper()
        if ev not in VALID_REVIEW_EVENTS:
            return {"ok": False, "status": 400, "error": f"Review event must be one of {sorted(VALID_REVIEW_EVENTS)}"}
        clean_body = (body or "").strip()
        if ev in ("REQUEST_CHANGES", "COMMENT") and not clean_body and not comments:
            return {"ok": False, "status": 400, "error": f"Review body is required for {ev}"}
        payload: dict = {"event": ev, "body": clean_body[:20_000]}
        if comments:
            sanitized = []
            for item in comments[:50]:
                if not isinstance(item, dict):
                    continue
                ok_p, clean_p = validate_repo_path(str(item.get("path") or ""), allow_empty=False)
                cbody = str(item.get("body") or "").strip()
                try:
                    ln = int(item.get("line") or 0)
                except (TypeError, ValueError):
                    ln = 0
                if ok_p and cbody and ln > 0:
                    sanitized.append({
                        "path": clean_p,
                        "line": ln,
                        "side": "LEFT" if str(item.get("side") or "RIGHT").upper() == "LEFT" else "RIGHT",
                        "body": cbody[:10_000],
                    })
            if sanitized:
                payload["comments"] = sanitized
        return self._req("POST", f"/repos/{enc_r}/pulls/{n}/reviews", payload)

    def merge_pr(self, full: str, number, *, merge_method: str = "merge",
                 commit_title: str | None = None, commit_message: str | None = None,
                 sha: str | None = None) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        method = (merge_method or "merge").strip().lower()
        if method not in VALID_MERGE_METHODS:
            return {"ok": False, "status": 400, "error": f"merge_method must be one of {sorted(VALID_MERGE_METHODS)}"}
        payload: dict = {"merge_method": method}
        if commit_title and commit_title.strip():
            payload["commit_title"] = commit_title.strip()[:256]
        if commit_message and commit_message.strip():
            payload["commit_message"] = commit_message.strip()[:20_000]
        if sha and sha.strip():
            payload["sha"] = sha.strip()
        r = self._req("PUT", f"/repos/{enc_r}/pulls/{n}/merge", payload)
        if not r.get("ok") and r.get("status") in (405, 409):
            r["conflict"] = True
        return r

    def close_pr(self, full: str, number) -> dict:
        ok_r, enc_r = _encode_repo(full)
        if not ok_r:
            return {"ok": False, "status": 400, "error": enc_r}
        ok_n, n = _parse_pr_number(number)
        if not ok_n:
            return {"ok": False, "status": 400, "error": str(n)}
        return self._req("PATCH", f"/repos/{enc_r}/pulls/{n}", {"state": "closed"})

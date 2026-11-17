"""Unified-diff parsing (shared by API server and mirrored in Kotlin DiffParser)."""
from __future__ import annotations


def parse_patch(patch: str) -> list[dict]:
    out: list[dict] = []
    for line in (patch or "").splitlines():
        kind = "context"
        if line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+") and not line.startswith("+++"):
            kind = "add"
        elif line.startswith("-") and not line.startswith("---"):
            kind = "del"
        out.append({"kind": kind, "text": line})
    return out


def summarize_pr_files(files_data: list[dict] | None) -> list[dict]:
    files: list[dict] = []
    for f in files_data or []:
        patch = f.get("patch") or ""
        files.append({
            "filename": f.get("filename", ""),
            "previous_filename": f.get("previous_filename"),
            "status": f.get("status", "modified"),
            "sha": f.get("sha", ""),
            "additions": int(f.get("additions") or 0),
            "deletions": int(f.get("deletions") or 0),
            "changes": int(f.get("changes") or 0),
            "binary_or_truncated": (not patch) and int(f.get("changes") or 0) == 0 and f.get("status") != "renamed",
            "lines": parse_patch(patch),
        })
    return files


def summarize_compare(data: dict | None, *, base: str = "", head: str = "") -> dict:
    payload = data or {}
    ahead = int(payload.get("ahead_by") or 0)
    behind = int(payload.get("behind_by") or 0)
    raw_status = payload.get("status") or (
        "diverged" if (ahead > 0 and behind > 0)
        else "ahead" if ahead > 0
        else "behind" if behind > 0
        else "identical"
    )
    files = summarize_pr_files(payload.get("files") or [])
    commits = []
    for c in payload.get("commits") or []:
        commit_obj = c.get("commit") or {}
        author_obj = commit_obj.get("author") or {}
        commits.append({
            "sha": (c.get("sha") or "")[:7],
            "full_sha": c.get("sha") or "",
            "message": (commit_obj.get("message") or "").splitlines()[0] if commit_obj.get("message") else "",
            "author": author_obj.get("name") or (c.get("author") or {}).get("login") or "",
            "date": author_obj.get("date") or "",
        })
    return {
        "base": base,
        "head": head,
        "status": raw_status,
        "ahead_by": ahead,
        "behind_by": behind,
        "diverged": bool(raw_status == "diverged" or (ahead > 0 and behind > 0)),
        "total_commits": int(payload.get("total_commits") or len(commits)),
        "base_commit_sha": ((payload.get("base_commit") or {}).get("sha") or ""),
        "merge_base_sha": ((payload.get("merge_base_commit") or {}).get("sha") or ""),
        "files": files,
        "commits": commits,
    }

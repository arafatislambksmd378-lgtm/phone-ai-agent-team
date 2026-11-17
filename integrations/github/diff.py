"""Unified-diff parsing (shared by API server and mirrored in Kotlin DiffParser)."""
from __future__ import annotations
import re
def parse_patch(patch: str):
    out=[]
    for line in (patch or "").splitlines():
        kind = "context"
        if line.startswith("@@"): kind="hunk"
        elif line.startswith("+") and not line.startswith("+++"): kind="add"
        elif line.startswith("-") and not line.startswith("---"): kind="del"
        out.append({"kind": kind, "text": line})
    return out
def summarize_compare(data: dict):
    files=[]
    for f in (data or {}).get("files", []):
        files.append({"filename": f.get("filename"), "status": f.get("status"), "additions": f.get("additions",0),
                      "deletions": f.get("deletions",0), "changes": f.get("changes",0), "lines": parse_patch(f.get("patch",""))})
    return {"ahead_by": (data or {}).get("ahead_by",0), "behind_by": (data or {}).get("behind_by",0),
            "total_commits": (data or {}).get("total_commits",0), "files": files,
            "commits": [{"sha": c.get("sha","")[:7], "message": (c.get("commit") or {}).get("message","")} for c in (data or {}).get("commits",[])]}

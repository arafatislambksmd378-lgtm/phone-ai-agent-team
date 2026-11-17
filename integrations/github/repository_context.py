"""Explicit, bounded, read-only repository context for a future isolated worker.

This module does not send data to a model, execute code, create files or enable
GitHub execution. Callers must obtain informed consent before transmitting any
private repository content to a model provider. Secret detection is heuristic.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from security.code_scanner import detect_high_confidence_secrets
from security.validation import validate_branch_name, validate_repo_path

MAX_FILES = 12
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET_NAMES = re.compile(r"(?i)(?:^|[._-])(secret|credential|private[_-]?key|token|password)(?:[._-]|$)")
_SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}


def collect_selected_context(service, project_id: str, paths: list[str], branch: str | None = None) -> dict:
    """Fetch specifically selected UTF-8 files at an immutable commit from the bound repo.

    Files are never persisted, logged, sent to AI, or written back to GitHub.
    Reject symlinks, credential-looking paths, mismatched blobs and excessive data.
    GitHub directory metadata is used to confirm file type before fetching content.
    """
    repo, err = service._bound_repo(project_id)
    if err:
        return err
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES or not all(isinstance(p, str) for p in paths):
        return {"ok": False, "status": 400, "error": f"Select between 1 and {MAX_FILES} file paths"}
    checked = []
    for path in paths:
        valid, clean = validate_repo_path(path, allow_empty=False)
        leaf = PurePosixPath(clean).name if valid else ""
        if not valid or _SECRET_NAMES.search(leaf) or PurePosixPath(leaf).suffix.lower() in _SECRET_EXTENSIONS:
            return {"ok": False, "status": 400, "error": "A selected path is invalid or may contain credentials"}
        if clean in checked:
            return {"ok": False, "status": 400, "error": "Duplicate selected paths are not allowed"}
        checked.append(clean)
    target, err = service.resolve_working_branch(project_id, branch)
    if err:
        return err
    valid, message = validate_branch_name(target or "")
    if not valid:
        return {"ok": False, "status": 400, "error": message}
    repo_name = repo["repo_full_name"]
    commit, remote = service.api.branch_sha(repo_name, target)
    if not commit or not _SHA.fullmatch(commit.lower()):
        return {"ok": False, "status": 502, "error": "Could not resolve a valid immutable branch commit"}
    commit = commit.lower()
    listings: dict[str, dict] = {}
    files = []
    total = 0
    for path in checked:
        parent = str(PurePosixPath(path).parent)
        if parent == ".":
            parent = ""
        if parent not in listings:
            result = service.api.list_dir(repo_name, parent, commit)
            if not result.get("ok"):
                return {"ok": False, "status": result.get("status") or 502, "error": "Could not list selected directory"}
            listings[parent] = {item.get("path"): item for item in result.get("data", []) if isinstance(item, dict)}
        entry = listings[parent].get(path)
        if not entry or entry.get("type") != "file":
            return {"ok": False, "status": 400, "error": f"Selected path is not a regular file: {path}"}
        if int(entry.get("size") or 0) > MAX_FILE_BYTES:
            return {"ok": False, "status": 413, "error": f"Selected file exceeds size limit: {path}"}
        result = service.api.read_text(repo_name, path, commit)
        if not result.get("ok"):
            return {"ok": False, "status": result.get("status") or 502, "error": f"Could not read selected file: {path}"}
        content = result.get("text")
        if not isinstance(content, str):
            return {"ok": False, "status": 502, "error": f"Invalid file contents: {path}"}
        encoded = content.encode("utf-8")
        total += len(encoded)
        if len(encoded) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            return {"ok": False, "status": 413, "error": "Selected repository content exceeds context size limit"}
        if detect_high_confidence_secrets(content):
            return {"ok": False, "status": 422, "error": f"Selected file contains a potential secret: {path}"}
        sha = hashlib.sha1(b"blob " + str(len(encoded)).encode("ascii") + b"\0" + encoded).hexdigest()
        if not _SHA.fullmatch(str(entry.get("sha") or "").lower()) or entry["sha"].lower() != sha or str(result.get("sha") or "").lower() != sha:
            return {"ok": False, "status": 409, "error": f"Git blob integrity mismatch: {path}"}
        files.append({"path": path, "blob_sha": sha, "size": len(encoded), "text": content})
    return {"ok": True, "status": 200, "repo": repo_name, "branch": target, "commit_sha": commit,
            "total_bytes": total, "files": files}

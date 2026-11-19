"""Project artifact packager — builds clean, secret-free deliverable ZIP archives."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "uploads", "extracted"}
EXCLUDED_FILES = {".env", "id_rsa", "id_ed25519"}


def package_project_zip(workspace: str | Path, dest_zip: str | Path | None = None) -> dict:
    """Create a clean deliverable ZIP of generated workspace artifacts and compute SHA-256."""
    ws = Path(workspace).resolve()
    if not ws.exists():
        return {"ok": False, "error": "Workspace does not exist"}

    out_path = Path(dest_zip).resolve() if dest_zip else (ws.parent / f"{ws.name}_deliverable.zip")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(ws.rglob("*")):
            if not f.is_file() or f.is_symlink():
                continue
            rel_parts = f.relative_to(ws).parts
            if any(part in EXCLUDED_DIRS for part in rel_parts):
                continue
            if f.name in EXCLUDED_FILES or f.name.endswith((".part", ".tmp", ".pyc")):
                continue
            if f.resolve() == out_path:
                continue
            arcname = str(f.relative_to(ws))
            zf.write(f, arcname=arcname)
            included.append(arcname)

    h = hashlib.sha256()
    with open(out_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)

    return {
        "ok": True,
        "zip_path": str(out_path),
        "file_count": len(included),
        "size_bytes": out_path.stat().st_size,
        "sha256": h.hexdigest(),
    }

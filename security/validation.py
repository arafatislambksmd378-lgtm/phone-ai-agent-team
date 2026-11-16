"""Input validation — ZIP traversal, file types, sizes. All uploads untrusted."""
from __future__ import annotations
import os
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx", ".json", ".zip", ".py", ".js", ".html", ".csv", ".yaml", ".yml", ".toml"}

def validate_upload(filename: str, size_bytes: int, max_mb: int = 20) -> tuple[bool, str]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported file type: {ext}"
    if size_bytes <= 0:
        return False, "Empty file"
    if size_bytes > max_mb * 1024 * 1024:
        return False, f"File too large (max {max_mb}MB)"
    return True, "OK"

def safe_extract_zip(zip_path: str, dest: str, max_files: int = 500, max_total_mb: int = 100) -> tuple[bool, str, list[str]]:
    """Extract with traversal + bomb protection. Returns (ok, msg, extracted_files)."""
    dest_p = Path(dest).resolve()
    dest_p.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                return False, f"Too many files in archive ({len(infos)} > {max_files})", []
            total = sum(i.file_size for i in infos)
            if total > max_total_mb * 1024 * 1024:
                return False, "Archive uncompressed size exceeds limit", []
            for info in infos:
                # Reject absolute paths and traversal
                name = info.filename
                if os.path.isabs(name) or ".." in Path(name).parts:
                    return False, f"Unsafe path in archive: {name}", []
                target = (dest_p / name).resolve()
                if not str(target).startswith(str(dest_p)):
                    return False, f"Path traversal blocked: {name}", []
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                extracted.append(str(target.relative_to(dest_p)))
        return True, f"Extracted {len(extracted)} files", extracted
    except zipfile.BadZipFile:
        return False, "Invalid ZIP archive", []
    except Exception as e:
        return False, f"Extraction failed: {e}", []

def safe_join(workspace: str, rel: str) -> Path:
    """Join rel under workspace, raising ValueError on escape."""
    root = Path(workspace).resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path escapes workspace: {rel}")
    return target

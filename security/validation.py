"""Input validation — ZIP traversal/bomb, file types, magic bytes, path confinement."""
from __future__ import annotations
import io
import os
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".json", ".zip",
    ".py", ".js", ".html", ".css", ".csv", ".yaml", ".yml", ".toml", ".sql", ".sh",
}

# Leading magic bytes for binary formats we accept.
MAGIC = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip",       # also docx (zip container)
    b"PK\x05\x06": "zip",       # empty archive
}


def sniff_type(path_or_bytes) -> str:
    """Return 'pdf' | 'zip' | 'text' | 'unknown' from magic bytes (first 8 bytes)."""
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            head = bytes(path_or_bytes[:8])
        else:
            with open(path_or_bytes, "rb") as fh:
                head = fh.read(8)
    except OSError:
        return "unknown"
    for magic, kind in MAGIC.items():
        if head.startswith(magic):
            return kind
    # Text heuristics: no NUL byte in the head.
    if head and b"\x00" not in head:
        return "text"
    return "unknown"


def validate_upload(filename: str, size_bytes: int, max_mb: int = 20) -> tuple[bool, str]:
    name = Path(filename or "").name
    ext = Path(name).suffix.lower()
    if not name or name.startswith("."):
        return False, "Invalid file name"
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported file type: {ext or '(none)'}"
    if size_bytes <= 0:
        return False, "Empty file"
    if size_bytes > max_mb * 1024 * 1024:
        return False, f"File too large (max {max_mb}MB)"
    return True, "OK"


def validate_upload_content(filename: str, path: str) -> tuple[bool, str]:
    """Second-stage check after download: declared extension must match real content."""
    ext = Path(filename).suffix.lower()
    kind = sniff_type(path)
    if ext == ".pdf" and kind != "pdf":
        return False, "File is not a valid PDF (magic bytes mismatch)"
    if ext in (".zip", ".docx") and kind != "zip":
        return False, f"File is not a valid {ext[1:].upper()} archive"
    if kind == "unknown":
        return False, "Unrecognised binary content rejected"
    return True, f"OK ({kind})"


def safe_join(workspace: str, rel: str) -> Path:
    """Join rel under workspace; raises ValueError on any escape (incl. sibling prefixes)."""
    root = os.path.realpath(workspace)
    if os.path.isabs(rel):
        candidate = os.path.realpath(rel)
    else:
        candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != root and os.path.commonpath([root, candidate]) != root:
        raise ValueError(f"Path escapes workspace: {rel}")
    return Path(candidate)


def safe_extract_zip(zip_path: str, dest: str, max_files: int = 500,
                     max_total_mb: int = 100, max_ratio: int = 200) -> tuple[bool, str, list[str]]:
    """Extract with traversal, count, size and compression-ratio (bomb) protection."""
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
            if total and hasattr(zip_path, "stat"):
                pass
            for info in infos:
                name = info.filename
                if os.path.isabs(name) or ".." in Path(name).parts:
                    return False, f"Unsafe path in archive: {name}", []
                target = (dest_p / name).resolve()
                if target != dest_p and os.path.commonpath([str(dest_p), str(target)]) != str(dest_p):
                    return False, f"Path traversal blocked: {name}", []
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                # Compression bomb: a decompressed member far larger than its compressed size.
                compressed = max(info.compress_size, 1)
                if info.file_size / compressed > max_ratio and info.file_size > 10 * 1024 * 1024:
                    return False, f"Suspicious compression ratio in {name} (bomb protection)", []
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size + 1:
                            raise ValueError(f"Stream larger than declared for {name}")
                        dst.write(chunk)
                extracted.append(str(target.relative_to(dest_p)))
        return True, f"Extracted {len(extracted)} files", extracted
    except zipfile.BadZipFile:
        return False, "Invalid ZIP archive", []
    except RuntimeError as e:  # encrypted archive
        return False, f"Encrypted archive not supported: {e}", []
    except Exception as e:
        return False, f"Extraction failed: {e}", []


def safe_zip_member_buffer(data: bytes) -> io.BytesIO:
    """Expose bytes as a zip source for tests/validation without touching disk."""
    return io.BytesIO(data)

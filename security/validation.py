"""Input validation — ZIP traversal/symlink/bomb protection, file types, magic bytes, path confinement."""
from __future__ import annotations
import io
import os
import stat
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".json", ".zip",
    ".py", ".js", ".ts", ".kt", ".java", ".html", ".css",
    ".csv", ".yaml", ".yml", ".toml", ".sql", ".sh", ".xml",
}

SUSPICIOUS_ARCHIVE_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".apk", ".dex", ".scr", ".com",
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
    if not name or name.startswith(".") or "\x00" in (filename or ""):
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
    if ext == ".docx":
        # Verify DOCX is actually an OpenXML document containing word/document.xml
        try:
            with zipfile.ZipFile(path) as zf:
                if "word/document.xml" not in zf.namelist():
                    return False, "File is not a valid DOCX (missing word/document.xml)"
        except Exception:
            return False, "File is not a valid DOCX archive"
    if kind == "unknown":
        return False, "Unrecognised binary content rejected"
    return True, f"OK ({kind})"


def safe_join(workspace: str, rel: str) -> Path:
    """Join rel under workspace; raises ValueError on any escape (incl. sibling prefixes & symlinks)."""
    if "\x00" in (rel or ""):
        raise ValueError("Null byte in path")
    root = os.path.realpath(workspace)
    if os.path.isabs(rel):
        candidate = os.path.realpath(rel)
    else:
        candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != root and os.path.commonpath([root, target := candidate]) != root:
        raise ValueError(f"Path escapes workspace: {rel}")
    return Path(candidate)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    """True when a ZIP member has POSIX S_IFLNK mode bits set."""
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _has_symlink_component(root: Path, target: Path) -> bool:
    """Ensure no intermediate path component inside root is a symbolic link."""
    cur = target
    root_resolved = root.resolve()
    while cur != root_resolved and cur != cur.parent:
        if cur.is_symlink():
            return True
        cur = cur.parent
    return False


def safe_extract_zip(zip_path: str, dest: str, max_files: int = 500,
                     max_total_mb: int = 100, max_ratio: int = 200,
                     max_archive_mb: int = 50,
                     max_depth: int = 12) -> tuple[bool, str, list[str]]:
    """Extract ZIP with path traversal, symlink escape, archive size, count, decompressed size & bomb protection."""
    dest_p = Path(dest).resolve()
    dest_p.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    try:
        if isinstance(zip_path, (str, Path)) and Path(zip_path).exists():
            arch_size = Path(zip_path).stat().st_size
            if arch_size > max_archive_mb * 1024 * 1024:
                return False, f"Archive file size exceeds {max_archive_mb}MB limit", []

        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                return False, f"Too many files in archive ({len(infos)} > {max_files})", []
            total = sum(i.file_size for i in infos)
            if total > max_total_mb * 1024 * 1024:
                return False, "Archive uncompressed size exceeds limit", []

            nested_zips = 0
            for info in infos:
                name = info.filename
                if not name or "\x00" in name:
                    return False, "Invalid archive member name", []
                # Normalize backslashes so Windows-crafted traversal (`..\..\evil`) is caught on POSIX too
                norm_name = name.replace("\\", "/")
                parts = Path(norm_name).parts
                if os.path.isabs(norm_name) or norm_name.startswith("/") or ".." in parts:
                    return False, f"Unsafe path in archive: {name}", []
                if parts and ":" in parts[0]:
                    return False, f"Unsafe drive path in archive: {name}", []
                if len(parts) > max_depth:
                    return False, f"Suspicious archive directory depth ({len(parts)} > {max_depth})", []
                if _is_zip_symlink(info):
                    return False, f"Symlink escape blocked in archive: {name}", []
                ext = Path(norm_name).suffix.lower()
                if ext in SUSPICIOUS_ARCHIVE_EXTENSIONS:
                    return False, f"Executable/binary member blocked in archive: {name}", []
                if ext == ".zip":
                    nested_zips += 1
                    if nested_zips > 3:
                        return False, "Suspicious nested ZIP archive structure blocked", []

                target = (dest_p / norm_name).resolve()
                if target != dest_p and os.path.commonpath([str(dest_p), str(target)]) != str(dest_p):
                    return False, f"Path traversal blocked: {name}", []
                if _has_symlink_component(dest_p, target):
                    return False, f"Symlink parent blocked: {name}", []

                if info.is_dir() or norm_name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                # Compression bomb check: high ratio on non-trivial payload
                compressed = max(info.compress_size, 1)
                if info.file_size / compressed > max_ratio and info.file_size > 1 * 1024 * 1024:
                    return False, f"Suspicious compression ratio in {name} (bomb protection)", []

                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    return False, f"Refusing to overwrite symlink: {name}", []

                written = 0
                max_bytes = max_total_mb * 1024 * 1024
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size + 1 or written > max_bytes:
                            dst.close()
                            target.unlink(missing_ok=True)
                            raise ValueError(f"Stream larger than declared for {name}")
                        dst.write(chunk)
                # Ensure extracted files are never executable even if archive had +x bits
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
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

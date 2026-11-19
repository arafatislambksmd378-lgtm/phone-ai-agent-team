"""Input validation — ZIP traversal/symlink/bomb protection, file types, magic bytes,
path confinement, Git ref/path validation, and fail-closed token policy."""
from __future__ import annotations

import io
import ipaddress
import os
import re
import stat
import zipfile
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".kt", ".kts", ".java",
    ".go", ".rs", ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".dart",
    ".html", ".css", ".sql", ".sh", ".yaml", ".yml", ".toml", ".xml",
    ".gradle", ".properties", ".ini", ".cfg",
}

DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".rst", ".docx", ".json", ".csv"}

ARCHIVE_EXTENSIONS = {".zip"}

ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS | SOURCE_EXTENSIONS | IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS

SUSPICIOUS_ARCHIVE_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".apk", ".dex", ".scr", ".com",
}

RESTRICTED_REPO_FILENAMES = {
    ".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".git-credentials", ".netrc",
}

MIN_API_TOKEN_LENGTH = 24
WEAK_TOKEN_VALUES = {
    "changeme", "password", "secret", "token", "admin", "default", "test",
    "bearer", "apikey", "api_key", "api-auth-token", "replace-me", "replaceme",
    "your-token-here", "your_token_here", "example-token-do-not-use",
    "123456789012345678901234", "abcdefghijklmnopqrstuvwx",
    "000000000000000000000000", "111111111111111111111111",
}

# Leading magic bytes for binary formats we accept.
MAGIC = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip",               # also docx (zip container)
    b"PK\x05\x06": "zip",               # empty archive
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,120}$")
_REPO_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._\-]{0,98}[A-Za-z0-9])?$")
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")


def is_loopback_host(host: str) -> bool:
    """True strictly for local loopback bind addresses (127.0.0.0/8, ::1, localhost)."""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h == "localhost":
        return True
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def validate_api_token(token: str | None, *, min_length: int = MIN_API_TOKEN_LENGTH) -> tuple[bool, str]:
    """Fail-closed check that an API bearer token is configured and high-entropy."""
    if token is None or not isinstance(token, str):
        return False, "API_AUTH_TOKEN is missing; refusing to run without authentication"
    if token != token.strip() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        if not token.strip():
            return False, "API_AUTH_TOKEN is empty; refusing to disable authentication"
        return False, "API_AUTH_TOKEN must contain only printable non-whitespace ASCII characters"
    if len(token) < min_length:
        return False, (
            f"API_AUTH_TOKEN is too short ({len(token)} chars; minimum {min_length}). "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    low = token.lower()
    if low in WEAK_TOKEN_VALUES or any(
        low.startswith(p) for p in ("changeme", "password", "replace_me", "replace-me", "your_token", "your-token")
    ):
        return False, "API_AUTH_TOKEN uses a known placeholder/weak value"
    if len(set(token)) < 8:
        return False, "API_AUTH_TOKEN has insufficient character entropy (require >= 8 distinct characters)"
    return True, "OK"


def validate_repo_full_name(repo: str) -> tuple[bool, str]:
    """Validate GitHub 'owner/name' format; rejects URLs, path traversal, and control chars."""
    raw = (repo or "").strip()
    if not raw or "\x00" in raw or "\\" in raw:
        return False, "Repository must be in 'owner/name' format"
    parts = raw.split("/")
    if len(parts) != 2:
        return False, "Repository must be in 'owner/name' format"
    owner, name = parts[0], parts[1]
    if not _REPO_OWNER_RE.fullmatch(owner):
        return False, f"Invalid repository owner: {owner!r}"
    if name in {".", ".."} or ".." in name or not _REPO_NAME_RE.fullmatch(name):
        return False, f"Invalid repository name: {name!r}"
    return True, "OK"


def validate_branch_name(branch: str) -> tuple[bool, str]:
    """Validate a Git branch ref name (blocks traversal, flag injection, and invalid ref syntax)."""
    b = (branch or "").strip()
    if not b:
        return False, "Branch name is required"
    if not _BRANCH_RE.fullmatch(b):
        return False, f"Invalid branch name: {b!r}"
    if (
        b.startswith(("/", "-", "."))
        or b.endswith(("/", ".", ".lock"))
        or ".." in b
        or "//" in b
        or "@{" in b
        or b == "@"
    ):
        return False, f"Disallowed Git branch name: {b!r}"
    for seg in b.split("/"):
        if not seg or seg.startswith(".") or seg.endswith(".lock"):
            return False, f"Invalid branch path segment in {b!r}"
    return True, "OK"


def validate_repo_path(path: str, *, allow_empty: bool = False) -> tuple[bool, str]:
    """Validate a relative file/directory path inside a GitHub repository."""
    if path is None:
        return (True, "") if allow_empty else (False, "File path is required")
    if "\x00" in path or "\\" in path:
        return False, "Invalid characters in repository path"
    cleaned = path.strip()
    if cleaned == "":
        return (True, "") if allow_empty else (False, "File path is required")
    if cleaned.startswith("/") or len(cleaned) > 512:
        return False, "Repository path must be a relative path (<= 512 chars)"
    cleaned = cleaned.rstrip("/")
    if not cleaned:
        return (True, "") if allow_empty else (False, "File path is required")
    segments = cleaned.split("/")
    for seg in segments:
        if not seg or seg in {".", ".."}:
            return False, f"Path traversal or empty segment rejected: {path!r}"
        if seg == ".git":
            return False, "Direct modification of .git internals is prohibited"
        if any(ord(c) < 32 for c in seg):
            return False, "Control characters prohibited in path"
    leaf = segments[-1]
    if leaf in RESTRICTED_REPO_FILENAMES or (leaf.startswith(".env") and leaf != ".env.example"):
        return False, f"Restricted credential filename prohibited: {leaf}"
    return True, cleaned


def classify_upload_category(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in ARCHIVE_EXTENSIONS:
        return "zip"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in SOURCE_EXTENSIONS:
        return "source"
    return "document"


def sniff_type(path_or_bytes) -> str:
    """Return 'pdf' | 'zip' | 'png' | 'jpeg' | 'gif' | 'webp' | 'text' | 'unknown' from magic bytes."""
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            head = bytes(path_or_bytes[:16])
        else:
            with open(path_or_bytes, "rb") as fh:
                head = fh.read(16)
    except OSError:
        return "unknown"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    for magic, kind in MAGIC.items():
        if head.startswith(magic):
            return kind
    # Reject known executable signatures explicitly even if printable prefix bytes appear (e.g. MZ/ELF)
    if head.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa")):
        return "unknown"
    # Text heuristics: no NUL byte in the head.
    if head and b"\x00" not in head:
        return "text"
    return "unknown"


def validate_upload(filename: str, size_bytes: int, max_mb: int = 20) -> tuple[bool, str]:
    name = Path(filename or "").name
    ext = Path(name).suffix.lower()
    if not name or name.startswith(".") or "\x00" in (filename or ""):
        return False, "Invalid file name"
    if "/" in name or "\\" in name or ".." in Path(filename or "").parts:
        return False, "Unsafe file name"
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
    if ext == ".png" and kind != "png":
        return False, "File is not a valid PNG image (magic bytes mismatch)"
    if ext in (".jpg", ".jpeg") and kind != "jpeg":
        return False, "File is not a valid JPEG image (magic bytes mismatch)"
    if ext == ".gif" and kind != "gif":
        return False, "File is not a valid GIF image (magic bytes mismatch)"
    if ext == ".webp" and kind != "webp":
        return False, "File is not a valid WebP image (magic bytes mismatch)"
    if ext in (DOCUMENT_EXTENSIONS | SOURCE_EXTENSIONS) - {".pdf", ".docx"} and kind != "text":
        return False, f"File {filename} does not contain valid text content"
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
    if candidate != root and os.path.commonpath([root, candidate]) != root:
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

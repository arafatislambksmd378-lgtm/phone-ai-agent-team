"""Safe file operations confined to project workspace. Memory-conscious."""
from __future__ import annotations
import hashlib
from pathlib import Path
from security.validation import safe_join

class FileManager:
    def __init__(self, workspace: str):
        self.workspace = str(Path(workspace).resolve())
        Path(self.workspace).mkdir(parents=True, exist_ok=True)

    def write(self, rel: str, content: str) -> str:
        p = safe_join(self.workspace, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def read(self, rel: str, max_bytes: int = 1_000_000) -> str:
        p = safe_join(self.workspace, rel)
        if not p.exists():
            raise FileNotFoundError(rel)
        return p.read_bytes()[:max_bytes].decode("utf-8", errors="replace")

    def exists(self, rel: str) -> bool:
        return safe_join(self.workspace, rel).exists()

    def list_files(self, rel: str = ".") -> list[str]:
        base = safe_join(self.workspace, rel)
        if not base.exists():
            return []
        out = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(str(f.relative_to(self.workspace)))
        return sorted(out)

    def sha256(self, rel: str) -> str:
        p = safe_join(self.workspace, rel)
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def delete(self, rel: str) -> bool:
        p = safe_join(self.workspace, rel)
        if p.is_file():
            p.unlink()
            return True
        return False

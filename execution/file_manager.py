"""Safe file operations confined to a project workspace with approval-gated destructive ops."""
from __future__ import annotations
import hashlib
import shutil
from pathlib import Path

from config.permissions import PROTECTED_FILENAMES
from security.approval import ApprovalRequiredError
from security.validation import safe_join

MAX_TEXT_BYTES = 1_000_000


class FileManager:
    def __init__(self, workspace: str, approval_manager=None, project_id: str = ""):
        self.workspace = str(Path(workspace).resolve())
        Path(self.workspace).mkdir(parents=True, exist_ok=True)
        self.approval_manager = approval_manager
        self.project_id = project_id

    def _p(self, rel: str) -> Path:
        # Project content must not be able to plant Git hooks or alter repository config.
        parts = rel.replace("\\", "/").split("/")
        if ".git" in parts:
            raise PermissionError("Git metadata is not accessible through the workspace file manager")
        return safe_join(self.workspace, rel)

    def _is_protected(self, rel: str) -> bool:
        norm = rel.strip("/").replace("\\", "/")
        return norm in PROTECTED_FILENAMES or Path(norm).name in {".env", "id_rsa", "id_ed25519"}

    def write(self, rel: str, content: str, approval_id: str | None = None) -> str:
        if self._is_protected(rel):
            self._enforce_approval("secret_operation", f"Write protected file: {rel}", approval_id)
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)  # atomic: a crash never leaves a half-written file
        return str(p)

    def read(self, rel: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
        p = self._p(rel)
        if not p.exists():
            raise FileNotFoundError(rel)
        return p.read_bytes()[:max_bytes].decode("utf-8", errors="replace")

    def exists(self, rel: str) -> bool:
        try:
            return self._p(rel).exists()
        except ValueError:
            return False

    def list_files(self, rel: str = ".", limit: int = 5000) -> list[str]:
        base = self._p(rel)
        if not base.exists():
            return []
        out: list[str] = []
        for f in sorted(base.rglob("*")):
            if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts:
                out.append(str(f.relative_to(self.workspace)))
                if len(out) >= limit:
                    break
        return out

    def sha256(self, rel: str) -> str:
        p = self._p(rel)
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def delete(self, rel: str, approval_id: str | None = None) -> bool:
        p = self._p(rel)
        if not p.exists():
            return False
        if self.approval_manager is not None:
            self._enforce_approval("delete_files", f"Delete workspace path: {rel}", approval_id)
        if p.is_file():
            p.unlink()
            return True
        if p.is_dir():
            shutil.rmtree(p)
            return True
        return False

    def _enforce_approval(self, action: str, detail: str, approval_id: str | None) -> None:
        if self.approval_manager is None:
            if not approval_id:
                raise ApprovalRequiredError(f"Operation requires human approval ({action}): {detail}",
                                            action=action)
            return
        ok, msg = self.approval_manager.verify(
            approval_id,
            project_id=self.project_id or None,
            action=action,
            actor="file_manager",
        )
        if not ok:
            req_id = ""
            if not approval_id and self.project_id:
                req_id = self.approval_manager.request(self.project_id, action, detail)
            raise ApprovalRequiredError(
                f"Operation blocked pending human approval ({msg})",
                approval_id=req_id or (approval_id or ""),
                action=action,
            )

    def tree(self, limit: int = 200) -> str:
        return "\n".join(self.list_files(limit=limit))

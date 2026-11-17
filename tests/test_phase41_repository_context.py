"""Explicit pinned repository context tests; GitHub is mocked, no AI or writes occur."""
import hashlib
from unittest.mock import Mock

from integrations.github.executor import GitHubExecutionService
from integrations.github.repository_context import collect_selected_context
from memory.database import Database


def make_service(tmp_path):
    db = Database(tmp_path / "context.db")
    pid = db.create_project("test", str(tmp_path))
    db.bind_repository(pid, "owner/project", "private", "develop", "feature")
    api = Mock()
    api.branch_sha.return_value = ("a" * 40, {"ok": True})
    content = "print('hello')\n"
    raw = content.encode()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    api.list_dir.return_value = {"ok": True, "data": [{"path": "src/app.py", "type": "file", "size": len(raw), "sha": blob}]}
    api.read_text.return_value = {"ok": True, "text": content, "sha": blob}
    return GitHubExecutionService(db, api), api, pid


def test_context_is_bound_and_pinned_to_commit(tmp_path):
    svc, api, pid = make_service(tmp_path)
    result = collect_selected_context(svc, pid, ["src/app.py"])
    assert result["ok"] and result["commit_sha"] == "a" * 40
    assert result["files"][0]["text"] == "print('hello')\n"
    api.branch_sha.assert_called_once_with("owner/project", "feature")
    api.list_dir.assert_called_once_with("owner/project", "src", "a" * 40)
    api.read_text.assert_called_once_with("owner/project", "src/app.py", "a" * 40)
    api.put_file.assert_not_called()


def test_invalid_secret_symlink_and_mismatch_rejected(tmp_path):
    svc, api, pid = make_service(tmp_path)
    for path in ("../x", ".env", "keys/private.key", "password.txt"):
        assert not collect_selected_context(svc, pid, [path])["ok"]
    api.list_dir.return_value["data"][0]["type"] = "symlink"
    assert not collect_selected_context(svc, pid, ["src/app.py"])["ok"]
    api.list_dir.return_value["data"][0]["type"] = "file"
    api.read_text.return_value["sha"] = "b" * 40
    assert collect_selected_context(svc, pid, ["src/app.py"])["status"] == 409


def test_context_rejects_secret_content_and_limits(tmp_path):
    svc, api, pid = make_service(tmp_path)
    api.read_text.return_value["text"] = "-----BEGIN PRIVATE KEY-----\nsecret"
    assert collect_selected_context(svc, pid, ["src/app.py"])["status"] == 422
    assert not collect_selected_context(svc, pid, ["a"] * 13)["ok"]
    assert not collect_selected_context(svc, pid, ["src/app.py", "src/app.py"])["ok"]

import zipfile, tempfile, os
from security.validation import validate_upload, safe_extract_zip
from security.secret_manager import redact
from config.permissions import is_command_allowed

def test_validate_upload():
    ok, _ = validate_upload("a.txt", 100)
    assert ok
    ok, _ = validate_upload("a.exe", 100)
    assert not ok
    ok, _ = validate_upload("a.txt", 999 * 1024 * 1024)
    assert not ok

def test_zip_traversal_blocked():
    with tempfile.TemporaryDirectory() as d:
        zp = os.path.join(d, "evil.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("../evil.txt", "x")
        ok, msg, _ = safe_extract_zip(zp, os.path.join(d, "out"))
        assert not ok and "traversal" in msg.lower() or "unsafe" in msg.lower()

def test_zip_ok():
    with tempfile.TemporaryDirectory() as d:
        zp = os.path.join(d, "ok.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("a/b.txt", "hello")
        ok, msg, files = safe_extract_zip(zp, os.path.join(d, "out"))
        assert ok and files == ["a/b.txt"]

def test_redact():
    assert "REDACTED" in redact("key sk-1234567890abcdef")
    assert "REDACTED" in redact("token 123456789:ABC-DEF-GHI-JKL-MNO-PQR-STU-VWX-YZ")

def test_permissions():
    assert is_command_allowed("ls -la") == (True, False)
    assert is_command_allowed("rm -rf /") == (False, False)
    assert is_command_allowed("rm file.txt") == (True, True)

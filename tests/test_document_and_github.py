"""Tests for DocumentProcessor (TXT/MD/JSON/CSV/PDF/DOCX/ZIP), GitHubClient, and ResourceManager."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from config.settings import Settings
from execution.document_processor import extract_document_text
from execution.file_manager import FileManager
from execution.process_manager import ProcessManager
from execution.resource_manager import ResourceManager
from integrations.github.client import GitHubClient
from memory.database import Database
from orchestrator.engine import OrchestratorEngine
from security.approval import ApprovalManager
from security.validation import safe_extract_zip


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    p_xml = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc_xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{ns}"><w:body>{p_xml}</w:body></w:document>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", doc_xml)


def _make_pdf(path: Path, text_line: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text_line}) Tj ET".encode("latin-1")
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Length " + str(len(stream)).encode() + b" >>\n"
        b"stream\n" + stream + b"\nendstream\n"
        b"endobj\n%%EOF\n"
    )
    path.write_bytes(pdf_bytes)


def test_document_processor_extracts_txt_md_json_csv_docx_pdf_and_code(tmp_path):
    # JSON
    j_path = tmp_path / "spec.json"
    j_path.write_text(json.dumps({"app": "Inventory", "endpoints": ["/items"]}), encoding="utf-8")
    j_res = extract_document_text(j_path)
    assert j_res["ok"] and j_res["kind"] == "json"
    assert "Inventory" in j_res["text"]

    # CSV
    c_path = tmp_path / "entities.csv"
    c_path.write_text("field,type,required\nsku,text,yes\nqty,integer,yes\n", encoding="utf-8")
    c_res = extract_document_text(c_path)
    assert c_res["ok"] and c_res["kind"] == "csv"
    assert "sku | text | yes" in c_res["text"]

    # DOCX
    d_path = tmp_path / "blueprint.docx"
    _make_docx(d_path, ["Build a CLI bookmark manager.", "Must export JSON."])
    d_res = extract_document_text(d_path)
    assert d_res["ok"] and d_res["kind"] == "docx"
    assert "bookmark manager" in d_res["text"]

    # PDF
    p_path = tmp_path / "spec.pdf"
    _make_pdf(p_path, "Build a REST API for expense tracking")
    p_res = extract_document_text(p_path)
    assert p_res["ok"] and p_res["kind"] == "pdf"
    assert "expense tracking" in p_res["text"]

    # Corrupted DOCX & fake PDF rejected cleanly without crashing
    bad_docx = tmp_path / "corrupt.docx"
    bad_docx.write_bytes(b"not-a-zip-at-all")
    assert not extract_document_text(bad_docx)["ok"]

    bad_pdf = tmp_path / "corrupt.pdf"
    bad_pdf.write_bytes(b"NOTPDF12345")
    assert not extract_document_text(bad_pdf)["ok"]


def test_engine_ingest_upload_docx_and_zip_populates_requirements(tmp_path):
    s = Settings(
        telegram_bot_token="t",
        telegram_allowed_user_id=1,
        workspace_root=tmp_path / "ws",
        db_path=tmp_path / "up.db",
    )
    engine = OrchestratorEngine(s)
    try:
        pid = engine.create_project("DocProj", auto_start=False)
        docx = tmp_path / "req.docx"
        _make_docx(docx, ["Build a headless REST API for books with SQLite."])
        ok, msg = engine.ingest_upload(pid, str(docx), "req.docx", auto_plan_if_waiting=False)
        assert ok, msg
        ws = engine.db.get_project(pid)["workspace"]
        req_txt = FileManager(ws).read("requirements.txt")
        assert "REST API for books" in req_txt
    finally:
        engine.shutdown()


def test_safe_extract_zip_blocks_executables_and_compression_bombs(tmp_path):
    # Block executable inside zip
    exe_zip = tmp_path / "payload.zip"
    with zipfile.ZipFile(exe_zip, "w") as zf:
        zf.writestr("readme.md", "hello")
        zf.writestr("bin/dropper.exe", b"MZ\x90\x00")
    ok, msg, _ = safe_extract_zip(str(exe_zip), str(tmp_path / "out1"))
    assert not ok
    assert "executable" in msg.lower() or "blocked" in msg.lower()

    # Block extreme compression ratio (>100:1 when >1MB uncompressed)
    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.txt", b"0" * (2 * 1024 * 1024))
    ok2, msg2, _ = safe_extract_zip(str(bomb_zip), str(tmp_path / "out2"))
    assert not ok2
    assert "compression ratio" in msg2.lower()


def test_github_client_secret_scan_remote_config_and_approval_gated_push(tmp_path):
    db = Database(tmp_path / "gh.db")
    approvals = ApprovalManager(db)
    gh = GitHubClient(repo="owner/demo", token="", approval_manager=approvals)

    # Create bare remote + local workspace
    bare = tmp_path / "remote.git"
    bare.mkdir()
    import subprocess
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    ws = tmp_path / "local_ws"
    ws.mkdir()
    pid = db.create_project("ghproj", str(ws))
    (ws / "app.py").write_text("print('clean')\n", encoding="utf-8")
    cp = gh.init_and_checkpoint(str(ws), "init clean")
    assert cp["ok"] and cp["commit"]

    # Reject credentials embedded directly in remote URL
    bad_rem = gh.configure_remote(str(ws), "https://user:ghp_secret@github.com/owner/repo.git")
    assert not bad_rem["ok"]

    # Valid local bare remote
    ok_rem = gh.configure_remote(str(ws), str(bare))
    assert ok_rem["ok"]

    # 1. Secret scan blocks leaked token before push
    (ws / "leaked.py").write_text("KEY = 'sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456'\n", encoding="utf-8")
    scan = gh.scan_before_publish(str(ws))
    assert not scan["ok"]
    blocked_push = gh.push(str(ws), project_id=pid, is_public_repo=True)
    assert not blocked_push["ok"] and blocked_push.get("blocked_by_secrets")
    (ws / "leaked.py").unlink()

    # 2. Unapproved push to public repo creates pending `public_github_release` approval and blocks
    unappr = gh.push(str(ws), project_id=pid, is_public_repo=True)
    assert not unappr["ok"] and unappr.get("needs_approval")
    aid = unappr["approval_id"]
    assert aid

    # 3. Once human approves, push succeeds and backup verification matches HEAD!
    approvals.approve(aid, by="owner")
    pushed = gh.push(str(ws), project_id=pid, approval_id=aid, is_public_repo=True)
    assert pushed["ok"], pushed
    verified = gh.verify_backup(str(ws))
    assert verified["ok"] and verified["mode"] == "remote_verified"
    db.close()


def test_resource_and_process_managers_enforce_limits_and_cleanup(tmp_path):
    pm = ProcessManager()
    # Subprocess timeout kills entire process group cleanly
    timed_out = pm.run_managed("slow", ["python3", "-c", "import time; time.sleep(10)"], cwd=str(tmp_path), timeout=1)
    assert not timed_out["ok"]
    assert timed_out.get("timed_out") is True
    assert pm.running() == []

    # ResourceManager throttles 2nd worker when threshold exceeds available RAM
    rm_strict = ResourceManager(workspace_root=tmp_path, min_free_ram_mb_worker2=999_999_999)
    assert rm_strict.effective_worker_limit(2) == 1

    # Temp file cleanup removes .tmp and .part files
    (tmp_path / "partial.part").write_bytes(b"abc")
    (tmp_path / "unfinished.tmp").write_bytes(b"xyz")
    removed = rm_strict.cleanup_workspace_temps(tmp_path)
    assert removed >= 2
    assert not (tmp_path / "partial.part").exists()


def test_required_scripts_and_documentation_exist():
    import subprocess
    repo_root = Path(__file__).resolve().parents[1]
    required_docs = [
        "README.md",
        ".env.example",
        "docs/INSTALLATION.md",
        "docs/CONFIGURATION.md",
        "docs/ARCHITECTURE.md",
        "docs/AGENT_GUIDE.md",
        "docs/SECURITY.md",
        "docs/RECOVERY.md",
        "docs/TESTING.md",
        "docs/PRODUCTION_READINESS.md",
        "docs/COMPREHENSIVE_AUDIT.md",
        "docs/IMPLEMENTATION_ROADMAP.md",
        "docs/REQUIREMENT_TRACEABILITY.md",
    ]
    for rel in required_docs:
        p = repo_root / rel
        assert p.is_file(), f"Missing required doc: {rel}"
        assert p.stat().st_size >= 400, f"Doc suspiciously empty: {rel}"

    required_scripts = [
        "scripts/install_termux.sh",
        "scripts/start_termux.sh",
        "scripts/stop_termux.sh",
        "scripts/healthcheck.sh",
    ]
    for rel in required_scripts:
        sp = repo_root / rel
        assert sp.is_file(), f"Missing required script: {rel}"
        chk = subprocess.run(["bash", "-n", str(sp)], capture_output=True, text=True)
        assert chk.returncode == 0, f"Syntax error in {rel}: {chk.stderr}"


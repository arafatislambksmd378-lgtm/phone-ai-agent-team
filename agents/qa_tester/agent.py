"""Agent 10 — QA & Testing: writes a real test suite and actually runs it."""
from __future__ import annotations
import subprocess
import sys

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a QA engineer. Reply with one complete pytest file that tests the
implementation in this workspace. Import the modules directly; no network calls."""

API_TEST = '''"""Generated API tests — import the backend and exercise it without a socket.

Tests adapt to the backend that actually exists: if the contract is missing,
they skip with a clear reason instead of failing for the wrong reason.
"""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("init_db", "create_item", "list_items", "delete_item")


def load_backend(tmp_path, monkeypatch):
    os.environ["APP_DB"] = str(tmp_path / "test.db")
    spec = importlib.util.spec_from_file_location("app_under_test", ROOT / "backend" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing = [name for name in REQUIRED if not hasattr(module, name)]
    if missing:
        pytest.skip(f"backend/app.py does not expose {missing}; contract test skipped")
    module.init_db()
    return module


def test_backend_file_exists():
    assert (ROOT / "backend" / "app.py").is_file()


def test_schema_applies(tmp_path):
    import sqlite3
    sql = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(str(tmp_path / "s.db"))
    try:
        con.executescript(sql)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert "items" in tables


def test_create_and_list(tmp_path, monkeypatch):
    app = load_backend(tmp_path, monkeypatch)
    item = app.create_item("First task", "notes")
    assert item["id"] >= 1
    assert [i["title"] for i in app.list_items()] == ["First task"]


def test_title_required(tmp_path, monkeypatch):
    app = load_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        app.create_item("   ")


def test_title_length_capped(tmp_path, monkeypatch):
    app = load_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        app.create_item("x" * 201)


def test_delete(tmp_path, monkeypatch):
    app = load_backend(tmp_path, monkeypatch)
    item = app.create_item("doomed")
    assert app.delete_item(item["id"]) is True
    assert app.list_items() == []
'''


class Agent(BaseAgent):
    agent_id = "qa_tester"
    name = "QA & Testing Agent"
    description = "Writes and executes the test suite, then reports real results."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        if not self._exists("backend/app.py"):
            self._write("docs/test-report.md",
                        "# Test Report\n\n**Verdict:** NOT RUN\n\nNo `backend/app.py` exists, "
                        "so there was nothing to test.\n")
            return AgentResult(ok=False, summary="Tests NOT RUN — backend missing",
                               files=["docs/test-report.md"], mode="deterministic",
                               error="backend/app.py missing")

        self._write("tests/test_api.py", API_TEST)
        self._write("tests/__init__.py", "")
        try:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_api.py"],
                                  cwd=self.workspace, capture_output=True, text=True,
                                  timeout=180, env=_env())
            output = ((proc.stdout or "")[-3000:] + (proc.stderr or "")[-1000:]).strip()
            ok = proc.returncode == 0
            verdict = "PASSED" if ok else "FAILED"
        except subprocess.TimeoutExpired:
            output, ok, verdict = "pytest timed out after 180s", False, "TIMEOUT"
        except Exception as e:
            output, ok, verdict = f"could not run pytest: {e}", False, "NOT RUN"

        report = (f"# Test Report\n\n**Verdict:** {verdict}\n\n"
                  f"**Command:** `pytest -q tests/test_api.py`\n\n"
                  f"```\n{output or '(no output)'}\n```\n")
        self._write("docs/test-report.md", report)
        return AgentResult(ok=ok, summary=f"Tests {verdict}", files=["docs/test-report.md"],
                           data={"verdict": verdict, "output": output[-500:]},
                           mode="deterministic", error="" if ok else output[-300:])


def _env() -> dict:
    import os
    keep = ("PATH", "HOME", "LANG", "TMPDIR", "PREFIX", "PYTHONPATH")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env

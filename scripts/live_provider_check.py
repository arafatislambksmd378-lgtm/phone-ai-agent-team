#!/usr/bin/env python3
"""Real Gemini verification + one real agent execution. Requires owner free/no-charge confirmation.

All dynamic text is passed through redact(); no credential value is ever printed or written.
Run only on a host with outbound provider access (GitHub Actions runner / Termux).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import Settings  # noqa: E402
from integrations.ai_providers import base as provider_base
from integrations.ai_providers.gemini import GeminiAdapter
from integrations.ai_providers.router import ModelRouter
from memory.database import Database
from orchestrator.engine import OrchestratorEngine
from security.secret_manager import redact

OUT = Path("live-evidence")
INVALID_KEY = "AIzaInvalidKeyForNegativeTest00000"  # deliberately not a real credential
RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, fn) -> None:
    try:
        detail = str(fn())
        RESULTS.append((name, True, redact(detail)[:900]))
        print(f"PASS {name}: {redact(detail)[:300]}")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        RESULTS.append((name, False, redact(detail)[:900]))
        print(f"FAIL {name}: {redact(detail)[:300]}")


def settings_for(db_path: Path, ws: Path, *, fallback: bool = False) -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        openai_api_key="", codex_api_key="", anthropic_api_key="",
        ai_model_gemini=os.getenv("GEMINI_MODEL", "") or "gemini-2.0-flash",
        ai_provider_order=["gemini"],
        allow_provider_fallback=fallback,
        ai_timeout_seconds=30,
        ai_max_tokens=512,
        db_path=db_path,
        workspace_root=ws,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    model = os.getenv("GEMINI_MODEL", "") or "gemini-2.0-flash"
    db_path = OUT / "provider.db"
    ws = OUT / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)

    def health_check() -> str:
        router = ModelRouter(settings_for(db_path, ws), db=db)
        h = router.health()
        assert h["mode"] == "live", h
        assert h["providers"]["gemini"]["status"] == "CONFIGURED", h
        return f"mode={h['mode']} model={h['providers']['gemini']['model']} fallback={h['fallback_enabled']}"

    def live_completion() -> str:
        router = ModelRouter(settings_for(db_path, ws), db=db)
        r = router.complete("chief_orchestrator", "Be concise.",
                            "Reply with the single word READY.", max_tokens=32)
        assert r.get("ok"), r
        assert r.get("mode") == "live" and not r.get("mock"), r
        assert r.get("provider") == "gemini", r
        assert "READY" in str(r.get("text", "")).upper(), r
        usage = db.query("SELECT provider,model,prompt_tokens,completion_tokens,cost_usd "
                         "FROM model_usage ORDER BY id DESC LIMIT 1")
        assert usage and usage[0]["provider"] == "gemini", usage
        return (f"provider={r['provider']} model={r['model']} mode={r['mode']} "
                f"tokens={r.get('prompt_tokens')}/{r.get('completion_tokens')} "
                f"usage_cost_usd={usage[0]['cost_usd']} response={str(r['text'])[:60]!r}")

    def invalid_key() -> str:
        adapter = GeminiAdapter(INVALID_KEY, model, timeout=30, max_retries=0)
        r = adapter.complete("system", "Reply with READY.", max_tokens=32)
        assert not r.get("ok"), "invalid key unexpectedly succeeded"
        status = r.get("status", "UNKNOWN")
        assert status in {"AUTH_ERROR", "BAD_REQUEST", "RATE_LIMITED", "SERVER_ERROR"}, r
        return f"status={status} http={r.get('http_status', '-')} (live request, no charge)"

    def timeout_classification() -> str:
        # Mocked transport: inducing a real timeout would waste quota; this verifies
        # that the adapter classifies timeouts instead of reporting a false success.
        original = provider_base.urllib.request.urlopen

        def _raise(*_args, **_kwargs):
            raise TimeoutError("mocked transport timeout")

        provider_base.urllib.request.urlopen = _raise
        try:
            r = GeminiAdapter(os.getenv("GEMINI_API_KEY", ""), model, timeout=2,
                              max_retries=0).complete("system", "READY", max_tokens=32)
        finally:
            provider_base.urllib.request.urlopen = original
        assert r.get("status") == "TIMEOUT" and not r.get("ok"), r
        return "status=TIMEOUT (mocked transport, not induced live)"

    def fallback() -> str:
        router = ModelRouter(settings_for(OUT / "fallback.db", ws, fallback=True),
                             db=Database(OUT / "fallback.db"))
        router.adapters["gemini"].api_key = INVALID_KEY  # force primary failure
        router.register_provider("gemini_backup", GeminiAdapter(
            os.getenv("GEMINI_API_KEY", ""), model, timeout=30, max_retries=0))
        router.cfg.provider_order = ["gemini", "gemini_backup"]
        r = router.complete("chief_orchestrator", "Be concise.",
                            "Reply with the single word READY.", max_tokens=32)
        assert r.get("ok") and r.get("mode") == "live", r
        assert r.get("provider") == "gemini_backup", r
        return f"primary_failed=True recovered_provider={r['provider']} mode={r['mode']}"

    def real_agent() -> str:
        agent_db = OUT / "agent.db"
        agent_ws = OUT / "agent-workspace"
        engine = OrchestratorEngine(settings_for(agent_db, agent_ws))
        try:
            pid = engine.create_project(
                "LiveAgentCheck",
                requirements="Build a tiny CLI tool that prints hello and includes one pytest test.",
                auto_start=False, source="live_workflow",
            )
            res = engine.create_single_task(
                pid, "requirement_analyst", "Extract live requirements",
                "Build a tiny CLI tool that prints hello and includes one pytest test.",
                auto_run=True, source="live_workflow",
            )
            assert res.get("ok") and res.get("id"), res
            tid = res["id"]
            deadline = time.time() + 150
            task = engine.db.get_task(tid)
            while time.time() < deadline and task["status"] not in {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}:
                time.sleep(1)
                task = engine.db.get_task(tid)
            assert task["status"] == "COMPLETED", task
            project_workspace = Path(engine.db.get_project(pid)["workspace"])
            spec_path = project_workspace / "docs" / "requirements.json"
            assert spec_path.is_file(), "agent produced no requirements.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            assert spec.get("mode") == "live" and spec.get("provider") == "gemini", spec
            usage = engine.db.query("SELECT provider,model,prompt_tokens,completion_tokens,cost_usd "
                                    "FROM model_usage WHERE project_id=?", (pid,))
            assert usage, "no token/cost record for the agent run"
            entries = engine.db.search_memory_entries(pid, kind="task_result")
            assert entries and entries[0]["source"] == "requirement_analyst", entries
            handoffs = engine.db.list_handoffs(pid)
            assert handoffs, "no verified handoff recorded"
            return (f"task={tid} status={task['status']} agent_mode={spec['mode']} "
                    f"provider={spec['provider']} usage_rows={len(usage)} "
                    f"memory_entries={len(entries)} handoffs={len(handoffs)}")
        finally:
            engine.shutdown()

    step("provider health reports LIVE", health_check)
    step("real Gemini completion returns live model output", live_completion)
    step("invalid key is rejected by real provider", invalid_key)
    step("timeout classification (mocked transport)", timeout_classification)
    step("provider fallback to healthy Gemini adapter", fallback)
    step("real agent execution through orchestrator", real_agent)
    try:
        db.close()
    except Exception:
        pass

    lines = ["# Live provider verification (Gemini)", "",
             "Owner confirmed: this key/model incurs no charge. No paid provider was enabled.",
             f"Model: {model}", ""]
    for name, passed, detail in RESULTS:
        lines.append(f"- {'PASS' if passed else 'FAIL'} {name}: {detail}")
    lines += ["", "Rate-limit (HTTP 429) and real timeout induction were NOT performed live;",
              "those paths remain covered by mocked-transport automated tests."]
    (OUT / "provider.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = all(passed for _, passed, _ in RESULTS)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} — {sum(p for _, p, _ in RESULTS)}/{len(RESULTS)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

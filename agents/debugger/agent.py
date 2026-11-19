"""Agent 11 — Debugging & Code Repair Agent: 7-step diagnosis, minimal safe repair, and regression re-test."""
from __future__ import annotations

import os
import re
import subprocess
import sys

from agents.base import AgentResult, BaseAgent
from agents.debugger.prompts import SYSTEM_PROMPT, build_repair_prompt
from agents.helpers import primary_entity_from, title_from, validate_python_syntax
from agents.software_architect.agent import render_backend_template


class Agent(BaseAgent):
    agent_id = "debugger"
    name = "Debugging & Code Repair Agent"
    description = "Reproduces failures, locates root causes, applies minimal safe code repairs, and runs regression tests."
    responsibilities = [
        "1. Identify actual failure from docs/test-report.md or live reproduction",
        "2. Reproduce failure deterministically where tests exist",
        "3. Locate responsible source component from tracebacks and syntax checks",
        "4. Apply minimal safe repair via AI Model Router or verified contract repair (preserving backup)",
        "5. Re-run relevant failing test module",
        "6. Run full workspace regression test suite",
        "7. Record factual before/after results in docs/debug-report.md",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate", "run_command"}
    output_schema = {
        "required_files_any": ["docs/debug-report.md"],
        "required_data_keys": ["initial_passed", "final_passed", "repairs", "unresolved"],
    }
    completion_criteria = ["docs/debug-report.md written with verified test status"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task, extra_files=["docs/test-report.md", "docs/requirements.json"])
        report = ctx["files"].get("docs/test-report.md") or self._read("docs/test-report.md")
        software_type = ctx["software_type"]

        # Step 1 & 2: Identify & reproduce actual failure
        test_files = [f for f in self._list() if f.startswith("tests/test_") and f.endswith(".py")]
        verdict_pass = ("PASSED" in report) or (report.strip().lower() == "passed")
        repro_output = report

        if not verdict_pass and test_files:
            ok_repro, out_repro = self._run_pytest(test_files)
            verdict_pass = ok_repro
            if out_repro:
                repro_output = out_repro

        repairs: list[str] = []
        unresolved: list[str] = []
        used_mode = "deterministic"

        # Ensure category-appropriate supporting artifacts exist if web_app
        if software_type == "web_app":
            if not self._exists("database/schema.sql"):
                from agents.database_engineer.agent import build_default_schema
                ent, _ = primary_entity_from(ctx["entities"], ctx["requirements"])
                self._write("database/schema.sql", build_default_schema(ent))
                repairs.append("created missing database/schema.sql")
            if not self._exists("frontend/index.html"):
                self._write("frontend/index.html", "<!doctype html><html><head><meta charset=utf-8><title>App</title></head><body></body></html>\n")
                repairs.append("created minimal frontend/index.html")

        # Step 3 & 4: Locate responsible component & apply minimal safe repair if failing
        if not verdict_pass:
            suspects = self._locate_suspect_files(repro_output)
            if not suspects:
                if software_type in ("web_app", "rest_api", "backend_service", "database_app"):
                    if not self._exists("backend/app.py"):
                        unresolved.append("backend/app.py missing — cannot stub an entire backend silently")
                    else:
                        suspects = ["backend/app.py"]
                elif software_type == "cli_tool" and self._exists("cli/main.py"):
                    suspects = ["cli/main.py"]
                elif software_type == "python_library":
                    suspects = [f for f in self._list() if f.startswith("src/") and f.endswith("core.py")][:2]
                elif software_type == "desktop_app" and self._exists("desktop/controller.py"):
                    suspects = ["desktop/controller.py"]
                elif software_type == "android_app" and self._exists("domain/model_verifier.py"):
                    suspects = ["domain/model_verifier.py"]

            for target in suspects:
                if not self._exists(target):
                    unresolved.append(f"{target} referenced in failure does not exist")
                    continue
                original_code = self._read(target)
                repaired_code, repair_mode, note = self._attempt_file_repair(
                    target, original_code, repro_output, ctx
                )
                used_mode = repair_mode
                if repaired_code and repaired_code != original_code:
                    self._write(target, repaired_code)
                    repairs.append(f"repaired `{target}` ({note})")
                else:
                    # Rollback/keep original untouched
                    unresolved.append(f"could not auto-repair `{target}` ({note})")

        # Step 5 & 6: Re-run relevant tests and full regression suite
        final_passed = verdict_pass
        regression_output = ""
        if (not verdict_pass or repairs) and test_files:
            ok_rel, out_rel = self._run_pytest(test_files)
            ok_reg, out_reg = self._run_pytest(["tests"])
            final_passed = ok_rel and ok_reg
            regression_output = out_reg or out_rel
            if final_passed:
                unresolved.clear()
                # Refresh docs/test-report.md so downstream ReleaseManager sees verified post-repair pass
                self._write(
                    "docs/test-report.md",
                    f"# Test Report\n\n**Verdict:** PASSED (after debugger repair)\n\n"
                    f"```\n{regression_output}\n```\n",
                )
            elif not unresolved:
                missing_names = sorted(set(re.findall(r"(?:NameError|ImportError|AttributeError).*?'(\w+)'", regression_output)))
                if missing_names:
                    unresolved.append("unresolved symbols: " + ", ".join(missing_names[:8]))
                else:
                    unresolved.append("test suite still failing after bounded repair attempt")

        # Step 7: Record factual debug report
        body = [
            "# Debug Report",
            "",
            f"- **Initial Verdict:** {'PASSED' if verdict_pass else 'FAILING'}",
            f"- **Final Verdict:** {'PASSED' if final_passed else 'FAILING'}",
            f"- **Mode:** `{used_mode}`",
            "",
            "## Repairs Applied",
            *([f"- {r}" for r in repairs] or ["- none required"]),
            "",
            "## Unresolved Issues",
            *([f"- {u}" for u in unresolved] or ["- none"]),
        ]
        if regression_output:
            body += ["", "## Regression Test Output", "```", regression_output[-2000:], "```"]
        body += ["", "## Initial Diagnostic Input", "```", (repro_output or "(empty)")[-2000:], "```"]
        self._write("docs/debug-report.md", "\n".join(body) + "\n")

        return AgentResult(
            ok=final_passed,
            summary=f"Debugger ({used_mode}): {len(repairs)} repair(s), {len(unresolved)} unresolved",
            files=["docs/debug-report.md"],
            data={
                "initial_passed": verdict_pass,
                "final_passed": final_passed,
                "repairs": repairs,
                "unresolved": unresolved,
            },
            mode=used_mode,
            error="" if final_passed else ("; ".join(unresolved) or "tests still failing"),
        )

    def _locate_suspect_files(self, failure_text: str) -> list[str]:
        suspects: list[str] = []
        # Check workspace Python files for syntax errors first
        for rel in self._list():
            if rel.endswith(".py") and not rel.startswith("tests/"):
                ok_s, _ = validate_python_syntax(self._read(rel), rel)
                if not ok_s and rel not in suspects:
                    suspects.append(rel)

        # Parse Python traceback paths pointing inside workspace
        for m in re.finditer(r'([A-Za-z0-9_./\-]+\.py)', failure_text or ""):
            cand = m.group(1).lstrip("./")
            if not cand.startswith("tests/") and self._exists(cand) and cand not in suspects:
                suspects.append(cand)
        return suspects[:3]

    def _attempt_file_repair(self, target: str, broken_code: str,
                             failure_output: str, ctx: dict) -> tuple[str, str, str]:
        prompt = build_repair_prompt(
            target_file=target,
            broken_code=broken_code,
            failure_output=failure_output,
            spec_md=ctx["requirements"],
        )
        gen = self.generate_code(prompt, "", prefer_tag="python", compile_filename=target)
        if gen.mode == "live" and gen.text.strip():
            return gen.text, "live", f"AI patch via {gen.provider}"

        # Deterministic safe repair for known syntax/contract failures in backend/app.py
        ok_syn, _ = validate_python_syntax(broken_code, target)
        if target == "backend/app.py" and (
            not ok_syn
            or any(err_kw in failure_output for err_kw in ("SyntaxError", "AttributeError", "NameError", "ImportError"))
        ):
            title = ctx["spec"].get("project_title") or title_from(ctx["requirements"])
            ent, _ = primary_entity_from(ctx["entities"], ctx["requirements"])
            return render_backend_template(title, ent), gen.mode, "restored verified backend contract"

        return broken_code, gen.mode, "no safe deterministic patch matched"

    def _run_pytest(self, targets: list[str]) -> tuple[bool, str]:
        self._require_tool("run_command")
        # Do not run untrusted workspace code on the service host: only a
        # verified isolated worker may execute project tests.
        from execution.isolation import executor, isolated_worker_available
        worker = executor() if isolated_worker_available() else None
        if worker is None or not self.project_id:
            from execution.isolation import BLOCKED_MESSAGE
            return False, BLOCKED_MESSAGE
        try:
            result = worker.run_pytest(
                project_id=self.project_id, workspace=self.workspace,
                target=" ".join(targets) if targets else "tests", timeout_seconds=180,
            )
        except Exception as exc:  # pragma: no cover - transport failure
            return False, f"isolated worker failed: {type(exc).__name__}: {exc}"
        output = ((result.get("stdout") or "")[-2500:]
                  + (result.get("stderr") or "")[-1000:]).strip()
        return bool(result.get("ok")), output

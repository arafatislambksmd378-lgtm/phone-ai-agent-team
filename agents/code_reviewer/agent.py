"""Agent 12 — Code Review & Optimization Agent: structural quality checks + AI optimization review."""
from __future__ import annotations

import re

from agents.base import AgentResult, BaseAgent
from agents.code_reviewer.prompts import SYSTEM_PROMPT, build_user_prompt

MAX_LINES = 500
MAX_FUNCTION_LINES = 70


class Agent(BaseAgent):
    agent_id = "code_reviewer"
    name = "Code Review & Optimization Agent"
    description = "Reviews generated source files for quality, duplication, silent errors, and 3 GB RAM efficiency."
    responsibilities = [
        "Inspect source modules for excessive length, long functions, bare excepts, TODOs, and duplication",
        "Provide concrete Android/Termux memory and query optimization recommendations",
        "Record structured findings in docs/review.md and AgentResult.data",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["docs/review.md"],
        "required_data_keys": ["reviewed_files", "issues"],
    }
    completion_criteria = ["docs/review.md written with accurate file and issue counts"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task)
        issues: list[dict] = []
        reviewed_files: list[str] = []

        for rel in self._list():
            if rel.startswith(("tests/", ".git/", "uploads/", "extracted/")):
                continue
            if not rel.endswith((".py", ".kt", ".js")):
                continue
            body = self._read(rel)
            if not body:
                continue
            reviewed_files.append(rel)
            lines = body.splitlines()
            if len(lines) > MAX_LINES:
                issues.append({"file": rel, "line": 1, "issue": f"file is {len(lines)} lines (>{MAX_LINES})"})

            if rel.endswith(".py"):
                for match in re.finditer(r"^def (\w+)\(", body, re.M):
                    start = body[: match.start()].count("\n")
                    end = len(lines)
                    for i in range(start + 1, len(lines)):
                        if lines[i].startswith(("def ", "class ")) and not lines[i].startswith(" "):
                            end = i
                            break
                    if end - start > MAX_FUNCTION_LINES:
                        issues.append({"file": rel, "line": start + 1,
                                       "issue": f"function {match.group(1)} is {end - start} lines"})
                if re.search(r"except\s*:\s*\n\s*pass", body):
                    issues.append({"file": rel, "line": 1, "issue": "bare silent exception swallowing"})

            if "TODO" in body or "FIXME" in body:
                issues.append({"file": rel, "line": 1, "issue": "unresolved TODO/FIXME marker"})

            counts: dict[str, int] = {}
            for line in lines:
                s = line.strip()
                if len(s) > 45 and not s.startswith(("#", "//")):
                    counts[s] = counts.get(s, 0) + 1
            dups = [s for s, n in counts.items() if n >= 5]
            if dups:
                issues.append({"file": rel, "line": 1, "issue": f"{len(dups)} line(s) repeated 5+ times"})

        fallback = self._fallback(len(reviewed_files), issues)
        prompt = build_user_prompt(
            reviewed_count=len(reviewed_files),
            issues=issues,
            software_type=ctx["software_type"],
            files=reviewed_files,
        )
        gen = self.generate(prompt, fallback)
        self._write("docs/review.md", gen.text if "#" in gen.text else fallback)

        return AgentResult(
            ok=True,
            summary=f"Review ({gen.mode}): {len(issues)} issue(s) in {len(reviewed_files)} file(s)",
            files=["docs/review.md"],
            data={"reviewed_files": len(reviewed_files), "issues": issues},
            mode=gen.mode,
            error=gen.error,
        )

    @staticmethod
    def _fallback(reviewed: int, issues: list[dict]) -> str:
        body_md = [
            "# Code Review",
            "",
            f"Files reviewed: {reviewed}",
            f"Issues: {len(issues)}",
            "",
        ]
        if issues:
            body_md += ["| File | Line | Finding |", "|---|---|---|"]
            body_md += [f"| `{i['file']}` | {i['line']} | {i['issue']} |" for i in issues[:80]]
        else:
            body_md += [
                "No structural issues detected. Reviewed for file length, function length, "
                "unresolved TODOs, bare silent failures, and duplicated blocks.",
            ]
        body_md += [
            "",
            "## Android / 3 GB RAM Optimisation Notes",
            "- Keep request/command handlers bounded in memory (cap payloads <= 1 MB).",
            "- Use SQLite WAL mode with indexed filter columns to avoid full table scans.",
            "- Stream file uploads and archive operations in 64 KB chunks.",
        ]
        return "\n".join(body_md) + "\n"

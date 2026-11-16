"""Agent 12 — Code Reviewer: measurable quality checks over the generated code."""
from __future__ import annotations
import re

from agents.base import AgentResult, BaseAgent

SYSTEM = """You are a code reviewer. Given files, report concrete issues: duplication,
dead code, long functions, missing error handling, unclear names. Cite file and line."""

MAX_LINES = 500
MAX_FUNCTION_LINES = 60


class Agent(BaseAgent):
    agent_id = "code_reviewer"
    name = "Code Review & Optimization Agent"
    description = "Reviews for quality, duplication and maintainability."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        issues: list[dict] = []
        reviewed = 0
        for rel in self._list():
            if not rel.endswith(".py") or rel.startswith(("tests/", ".git/")):
                continue
            body = self._read(rel)
            if not body:
                continue
            reviewed += 1
            lines = body.splitlines()
            if len(lines) > MAX_LINES:
                issues.append({"file": rel, "line": 0,
                               "issue": f"file is {len(lines)} lines (>{MAX_LINES})"})
            for match in re.finditer(r"^def (\w+)\(", body, re.M):
                start = body[:match.start()].count("\n")
                end = start + 1
                for i in range(start + 1, len(lines)):
                    if lines[i].startswith(("def ", "class ")) and not lines[i].startswith(" "):
                        end = i
                        break
                else:
                    end = len(lines)
                if end - start > MAX_FUNCTION_LINES:
                    issues.append({"file": rel, "line": start + 1,
                                   "issue": f"function {match.group(1)} is {end - start} lines"})
            if "TODO" in body or "FIXME" in body:
                issues.append({"file": rel, "line": 0, "issue": "unresolved TODO/FIXME"})
            if re.search(r"except\s*:\s*\n\s*pass", body):
                issues.append({"file": rel, "line": 0, "issue": "silent exception swallowing"})
            if re.search(r"print\(", body) and "backend" in rel:
                issues.append({"file": rel, "line": 0, "issue": "print() in library code"})
            # naive duplication signal: identical non-trivial lines repeated 5+ times
            counts: dict[str, int] = {}
            for line in lines:
                s = line.strip()
                if len(s) > 40 and not s.startswith("#"):
                    counts[s] = counts.get(s, 0) + 1
            dups = [s for s, n in counts.items() if n >= 5]
            if dups:
                issues.append({"file": rel, "line": 0,
                               "issue": f"{len(dups)} line(s) repeated 5+ times"})

        body_md = [f"# Code Review", "", f"Files reviewed: {reviewed}", f"Issues: {len(issues)}", ""]
        if issues:
            body_md += ["| File | Line | Finding |", "|---|---|---|"]
            body_md += [f"| `{i['file']}` | {i['line']} | {i['issue']} |" for i in issues[:80]]
        else:
            body_md += ["No structural issues detected. Reviewed for file length, function",
                        "length, TODOs, silent failures and duplicated lines."]
        body_md += ["", "## Optimisation notes", "",
                    "- Keep request handling O(1) per request; SQLite indexes cover reads.",
                    "- Avoid loading whole files into memory above 1 MB.",
                    "- Cache compiled regexes and reuse HTTP connections where possible."]
        self._write("docs/review.md", "\n".join(body_md))
        return AgentResult(ok=True, summary=f"Review: {len(issues)} issue(s) in {reviewed} file(s)",
                           files=["docs/review.md"], data={"issues": issues}, mode="deterministic")

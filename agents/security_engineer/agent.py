"""Agent 9 — Security Engineer: static audit of the generated workspace."""
from __future__ import annotations
import re

from agents.base import AgentResult, BaseAgent
from security.secret_manager import contains_secret

SYSTEM = """You are an application security reviewer. Given source files, list concrete
vulnerabilities with file, severity, evidence and fix. Be terse; no generic advice."""

# (pattern, severity, message)
RULES = [
    (re.compile(r"\beval\s*\("), "high", "dynamic eval of input"),
    (re.compile(r"\bexec\s*\("), "high", "dynamic exec of input"),
    (re.compile(r"shell\s*=\s*True"), "high", "subprocess with shell=True"),
    (re.compile(r"os\.system\s*\("), "high", "os.system usage"),
    (re.compile(r"pickle\.loads?\s*\("), "high", "unsafe pickle deserialization"),
    (re.compile(r"verify\s*=\s*False"), "medium", "TLS verification disabled"),
    (re.compile(r"SELECT \* FROM .*\+"), "high", "possible SQL string concatenation"),
    (re.compile(r"debug\s*=\s*True"), "low", "debug mode enabled"),
    (re.compile(r"0\.0\.0\.0"), "low", "binds to all interfaces (expected for Termux LAN use)"),
]

SECRET_HINT = re.compile(r"(sk-|AIza|ghp_|BEGIN (RSA|OPENSSH) PRIVATE KEY)")


class Agent(BaseAgent):
    agent_id = "security_engineer"
    name = "Security Engineer"
    description = "Audits the generated code for vulnerabilities and leaked secrets."
    system_prompt = SYSTEM

    def run(self, task: dict) -> AgentResult:
        findings: list[dict] = []
        scanned = 0
        for rel in self._list():
            if rel.startswith(".git/") or "/.git/" in rel:
                continue
            if not rel.lower().endswith((".py", ".js", ".html", ".sql", ".sh", ".json", ".md", ".toml", ".yml", ".yaml")):
                continue
            body = self._read(rel)
            if not body:
                continue
            scanned += 1
            for pattern, severity, message in RULES:
                for m in pattern.finditer(body):
                    line = body[:m.start()].count("\n") + 1
                    findings.append({"file": rel, "line": line, "severity": severity, "issue": message})
            if SECRET_HINT.search(body) or contains_secret(body):
                findings.append({"file": rel, "line": 0, "severity": "critical",
                                 "issue": "possible hardcoded secret"})
        by_sev: dict[str, int] = {}
        for f in findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        report = [f"# Security Review", "",
                  f"Files scanned: {scanned}",
                  f"Findings: {len(findings)} "
                  f"({', '.join(f'{k}={v}' for k, v in sorted(by_sev.items())) or 'none'})", ""]
        if findings:
            report += ["| Severity | File | Line | Issue |", "|---|---|---|---|"]
            report += [f"| {f['severity']} | `{f['file']}` | {f['line']} | {f['issue']} |"
                       for f in findings[:100]]
        else:
            report += ["No issues matched the audit rules.", "",
                       "Rules checked: eval/exec, shell=True, os.system, pickle, TLS bypass,",
                       "SQL concatenation, debug mode, bind address, secret patterns."]
        report += ["", "## Recommendations", "",
                   "- Keep secrets in `.env`; never write them into the app workspace.",
                   "- Validate every request body and cap its size.",
                   "- Run untrusted commands only through the allowlisted runner."]
        self._write("docs/security.md", "\n".join(report))
        return AgentResult(ok=True,
                           summary=f"Security audit: {len(findings)} finding(s) across {scanned} file(s)",
                           files=["docs/security.md"], data={"findings": findings}, mode="deterministic")

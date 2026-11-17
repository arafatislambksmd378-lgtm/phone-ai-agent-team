"""Agent 9 — Security Engineer: static vulnerability & secret audit + AI security synthesis."""
from __future__ import annotations

import re

from agents.base import AgentResult, BaseAgent
from agents.security_engineer.prompts import SYSTEM_PROMPT, build_user_prompt
from security.secret_manager import contains_secret

RULES = [
    (re.compile(r"\beval\s*\("), "high", "dynamic eval() of input"),
    (re.compile(r"\bexec\s*\("), "high", "dynamic exec() of input"),
    (re.compile(r"shell\s*=\s*True"), "high", "subprocess with shell=True"),
    (re.compile(r"os\.system\s*\("), "high", "os.system() command execution"),
    (re.compile(r"pickle\.loads?\s*\("), "high", "unsafe pickle deserialization"),
    (re.compile(r"verify\s*=\s*False"), "medium", "TLS certificate verification disabled"),
    (re.compile(r"SELECT\s+\*\s+FROM\s+.*\+"), "high", "possible SQL string concatenation"),
    (re.compile(r"android:exported\s*=\s*\"true\"(?![\s\S]{0,200}intent-filter)"), "medium",
     "exported Android component without intent-filter check"),
    (re.compile(r"debug\s*=\s*True"), "low", "debug mode enabled"),
]

SECRET_HINT = re.compile(r"(sk-[A-Za-z0-9_\-]{12,}|AIza[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{20,}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)")
SCAN_EXTS = (".py", ".js", ".ts", ".kt", ".java", ".html", ".sql", ".sh", ".json", ".toml", ".yml", ".yaml", ".xml")


class Agent(BaseAgent):
    agent_id = "security_engineer"
    name = "Security Engineer"
    description = "Audits generated workspace files for vulnerabilities, injection patterns, and leaked secrets."
    responsibilities = [
        "Scan all generated code and configuration files for dangerous patterns and leaked secrets",
        "Classify findings by severity (critical, high, medium, low)",
        "Produce actionable remediation guidance via AI Model Router + deterministic static rules",
    ]
    system_prompt = SYSTEM_PROMPT
    allowed_tools = {"read_file", "write_file", "list_files", "ai_generate"}
    output_schema = {
        "required_files_any": ["docs/security.md"],
        "required_data_keys": ["scanned_files", "findings", "critical_count"],
    }
    completion_criteria = ["docs/security.md written with accurate scan counts and finding severities"]

    def execute(self, task: dict) -> AgentResult:
        ctx = self.build_context(task)
        findings: list[dict] = []
        scanned_files: list[str] = []

        for rel in self._list():
            if rel.startswith((".git/", "uploads/", "extracted/", "docs/security.md")):
                continue
            if not rel.lower().endswith(SCAN_EXTS):
                continue
            body = self._read(rel)
            if not body:
                continue
            scanned_files.append(rel)
            for pattern, severity, message in RULES:
                for m in pattern.finditer(body):
                    line = body[: m.start()].count("\n") + 1
                    findings.append({"file": rel, "line": line, "severity": severity, "issue": message})
            if SECRET_HINT.search(body) or contains_secret(body):
                findings.append({"file": rel, "line": 1, "severity": "critical",
                                 "issue": "possible hardcoded secret or credential"})

        by_sev: dict[str, int] = {}
        for f in findings:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

        fallback = self._fallback_report(len(scanned_files), findings, by_sev)
        prompt = build_user_prompt(
            scanned_count=len(scanned_files),
            findings=findings,
            file_list=scanned_files,
            software_type=ctx["software_type"],
        )
        gen = self.generate(prompt, fallback)
        # Preserve critical marker fidelity so ReleaseManager gate is never misled by AI prose
        report_md = gen.text if "#" in gen.text else fallback
        if by_sev.get("critical", 0) == 0 and "critical" in report_md.lower():
            report_md = fallback

        self._write("docs/security.md", report_md)
        return AgentResult(
            ok=True,
            summary=f"Security audit ({gen.mode}): {len(findings)} finding(s) across {len(scanned_files)} file(s)",
            files=["docs/security.md"],
            data={
                "scanned_files": len(scanned_files),
                "findings": findings,
                "by_severity": by_sev,
                "critical_count": by_sev.get("critical", 0),
            },
            mode=gen.mode,
            error=gen.error,
        )

    @staticmethod
    def _fallback_report(scanned: int, findings: list[dict], by_sev: dict[str, int]) -> str:
        sev_str = ", ".join(f"{k}={v}" for k, v in sorted(by_sev.items())) or "none"
        report = [
            "# Security Review",
            "",
            f"- **Files scanned:** {scanned}",
            f"- **Findings:** {len(findings)} ({sev_str})",
            "",
        ]
        if findings:
            report += ["| Severity | File | Line | Issue |", "|---|---|---|---|"]
            report += [
                f"| {f['severity']} | `{f['file']}` | {f['line']} | {f['issue']} |"
                for f in findings[:100]
            ]
        else:
            report += [
                "No high-risk or secret-leak issues matched static audit rules.",
                "",
                "Rules checked: `eval`/`exec`, `shell=True`, `os.system`, unsafe `pickle`, "
                "TLS bypass, SQL string concatenation, Android manifest exports, and secret patterns.",
            ]
        report += [
            "",
            "## Security Controls",
            "- Keep credentials exclusively in `.env` (`chmod 600`); never commit secrets.",
            "- Enforce request body size caps and parameterized SQLite queries.",
            "- Run external commands exclusively through allowlisted `shell=False` execution.",
        ]
        return "\n".join(report) + "\n"

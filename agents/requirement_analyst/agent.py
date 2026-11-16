"""Agent 2: Requirement Analyst — Extracts and structures requirements from docs/ZIP/text."""
from agents.base import BaseAgent, AgentResult
import json, re

class Agent(BaseAgent):
    agent_id = "requirement_analyst"
    name = "Requirement Analyst"
    description = "Extracts and structures requirements from docs/ZIP/text."
    system_prompt = "You are the Requirement Analyst. Extracts and structures requirements from docs/ZIP/text. Be concise, secure, and produce file-based outputs."

    def run(self, task: dict) -> AgentResult:
        payload = self._payload(task)
        req_text = payload.get("requirements", "") or self._read("requirements.txt") or "Build a web application."
        # Extract functional requirements (lines with must/should/user/shall or bullet lines)
        lines = [l.strip("- *\t") for l in req_text.splitlines() if l.strip()]
        frs, nfrs, ambiguities = [], [], []
        for l in lines:
            low = l.lower()
            if any(k in low for k in ["must", "shall", "should", "user", "feature", "support"]):
                frs.append(l)
            elif any(k in low for k in ["fast", "secure", "scalable", "performance", "offline", "android", "mobile"]):
                nfrs.append(l)
            else:
                frs.append(l)
        if not frs:
            frs = [req_text[:500]]
        for f in frs:
            if len(f) < 8 or "etc" in f.lower() or "TBD" in f:
                ambiguities.append(f)
        spec = {"functional_requirements": frs, "non_functional": nfrs, "ambiguities": ambiguities,
                "raw_length": len(req_text), "project_title": (frs[0][:80] if frs else "Untitled")}
        self._write("docs/requirements.md", "# Requirements\n\n## Functional\n" + "\n".join(f"- {x}" for x in frs) + "\n\n## Non-functional\n" + "\n".join(f"- {x}" for x in nfrs) + "\n\n## Ambiguities\n" + "\n".join(f"- {x}" for x in ambiguities))
        self._write("docs/requirements.json", json.dumps(spec, indent=2))
        return AgentResult(ok=True, summary=f"Extracted {len(frs)} FRs, {len(nfrs)} NFRs, {len(ambiguities)} ambiguities.", files=["docs/requirements.md", "docs/requirements.json"], data=spec)

"""Shared agent helpers — code/JSON block extraction, validation, spec & category helpers."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass


@dataclass
class GenResult:
    text: str
    mode: str          # "live" | "mock" | "failed" | "deterministic"
    provider: str = ""
    model: str = ""
    error: str = ""


FENCE_RE = re.compile(r"```(?:[A-Za-z0-9_+\-#.]*)?\s*\n(.*?)```", flags=re.DOTALL)
FILE_BLOCK_RE = re.compile(
    r"(?:^|\n)(?:#+\s*FILE:\s*|//\s*FILE:\s*|#\s*FILE:\s*)`?([A-Za-z0-9_./\-]+)`?\s*\n"
    r"```(?:[A-Za-z0-9_+\-#.]*)?\s*\n(.*?)```",
    flags=re.DOTALL,
)


def extract_code_block(text: str, prefer_tag: str = "") -> str:
    """Strip Markdown triple-backtick fences from LLM code responses safely."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if prefer_tag:
        tagged = re.findall(
            rf"```{re.escape(prefer_tag)}[A-Za-z0-9_+\-]*\s*\n(.*?)```",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if tagged:
            return max(tagged, key=len).strip()
    blocks = FENCE_RE.findall(raw)
    if blocks:
        return max(blocks, key=len).strip()
    return raw


def extract_json_block(text: str) -> dict:
    """Extract a JSON object from raw text or a Markdown code block."""
    raw = (text or "").strip()
    if not raw:
        return {}
    candidate = extract_code_block(raw, prefer_tag="json")
    for attempt in (candidate, raw):
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(raw[start: end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def extract_file_blocks(text: str) -> dict[str, str]:
    """Parse multi-file AI responses formatted with `### FILE: rel/path.ext` blocks."""
    out: dict[str, str] = {}
    for rel, code in FILE_BLOCK_RE.findall(text or ""):
        clean_rel = rel.strip().lstrip("/")
        if clean_rel and ".." not in clean_rel.split("/"):
            out[clean_rel] = code.rstrip() + "\n"
    return out


def validate_python_syntax(code: str, filename: str = "<generated>") -> tuple[bool, str]:
    try:
        compile(code, filename, "exec")
        return True, "ok"
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg}"


def slugify(text: str, fallback: str = "app") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "")).strip("_").lower()
    if s and s[0].isdigit():
        s = f"pkg_{s}"
    return (s or fallback)[:40]


def slugify_dash(text: str, fallback: str = "app") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "")).strip("-").lower()
    return (s or fallback)[:40]


def extract_spec(raw: str) -> dict:
    """Best-effort structured requirements from free text or JSON."""
    if not raw:
        return {}
    return extract_json_block(raw)


def feature_lines(text: str, limit: int = 12) -> list[str]:
    """Pull candidate feature bullets from a requirements document."""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip().lstrip("-*•0123456789.) \t")
        if 3 <= len(s) <= 180 and not s.startswith(("#", "```", "http", "---")):
            out.append(s)
        if len(out) >= limit:
            break
    return out


def title_from(text: str, fallback: str = "Generated App") -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("# ").strip()
        if s.lower().startswith("specification —"):
            s = s.split("—", 1)[1].strip()
        if 3 <= len(s) <= 80 and not s.startswith("---"):
            return s
    return fallback


def primary_entity_from(entities: list[str] | None, requirements_text: str = "") -> tuple[str, str]:
    """Return (plural_table_name, singular_name) safely normalized as SQL/Python identifiers."""
    cand = ""
    if entities:
        cand = str(entities[0]).strip().lower()
    if not cand and requirements_text:
        m = re.search(
            r"\b(books|orders|notes|habits|expenses|contacts|events|tickets|invoices|tasks|todos|items)\b",
            requirements_text.lower(),
        )
        if m:
            cand = m.group(1)
    cand = re.sub(r"[^a-z0-9_]", "", cand) or "items"
    if cand in ("commands", "records", "resources", "entries"):
        if cand == "entries":
            return "entries", "entry"
        return cand, cand[:-1]
    if not cand.endswith("s"):
        cand = cand + "s"
    singular = cand[:-3] + "y" if cand.endswith("ies") else cand[:-1]
    return cand, singular or "item"

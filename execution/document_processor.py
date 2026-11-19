"""Secure document and source-code text extraction (stdlib only, Termux-safe).

Supports TXT, Markdown, JSON, CSV, PDF, DOCX, ZIP file summaries, and source code.
All extracted content is treated strictly as untrusted project specification data —
never executed or evaluated.
"""
from __future__ import annotations

import csv
import io
import json
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".kt", ".kts", ".java", ".go", ".rs", ".c", ".cpp", ".h",
    ".rb", ".php", ".swift", ".dart", ".html", ".css",
    ".yaml", ".yml", ".toml", ".sql", ".sh", ".xml",
    ".gradle", ".properties", ".ini", ".cfg",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

MAX_DECOMPRESSED_XML = 10 * 1024 * 1024   # 10 MB XML bomb cap for DOCX
MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024    # 8 MB per PDF stream decompression cap


def extract_document_text(path: str | Path, filename: str | None = None,
                          max_chars: int = 80_000) -> dict:
    """Extract clean readable text from a validated upload without executing anything.

    Returns:
      {"ok": bool, "kind": str, "text": str, "chars": int, "warning": str}
    """
    p = Path(path)
    name = filename or p.name
    ext = Path(name).suffix.lower()
    if not p.exists() or not p.is_file():
        return {"ok": False, "kind": "missing", "text": "", "chars": 0, "warning": "File not found"}

    try:
        if ext == ".pdf":
            text = _extract_pdf(p, max_chars=max_chars)
            return {"ok": True, "kind": "pdf", "text": text[:max_chars],
                    "chars": len(text[:max_chars]),
                    "warning": "" if text.strip() else "PDF contained no extractable text streams"}
        if ext == ".docx":
            text = _extract_docx(p, max_chars=max_chars)
            return {"ok": True, "kind": "docx", "text": text[:max_chars],
                    "chars": len(text[:max_chars]), "warning": ""}
        if ext == ".json":
            text = _extract_json(p, max_chars=max_chars)
            return {"ok": True, "kind": "json", "text": text[:max_chars],
                    "chars": len(text[:max_chars]), "warning": ""}
        if ext == ".csv":
            text = _extract_csv(p, max_chars=max_chars)
            return {"ok": True, "kind": "csv", "text": text[:max_chars],
                    "chars": len(text[:max_chars]), "warning": ""}
        if ext in IMAGE_EXTENSIONS:
            desc = _extract_image_metadata(p, name, ext)
            return {"ok": True, "kind": "image", "text": desc[:max_chars],
                    "chars": len(desc[:max_chars]), "warning": ""}
        if ext in TEXT_EXTENSIONS:
            raw = p.read_bytes()[: max_chars * 2].decode("utf-8", errors="replace")
            return {"ok": True, "kind": ext.lstrip(".") or "text", "text": raw[:max_chars],
                    "chars": len(raw[:max_chars]), "warning": ""}
        return {"ok": False, "kind": "unsupported", "text": "", "chars": 0,
                "warning": f"Unsupported document type: {ext}"}
    except Exception as e:
        return {"ok": False, "kind": ext.lstrip(".") or "error", "text": "", "chars": 0,
                "warning": f"Document extraction error: {e}"}


def extract_directory_corpus(directory: str | Path, max_files: int = 25,
                             max_chars: int = 80_000) -> str:
    """Extract text across safe files inside uploads/ or extracted/ directories."""
    base = Path(directory)
    if not base.exists() or not base.is_dir():
        return ""
    sections: list[str] = []
    used_chars = 0
    count = 0
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        ext = f.suffix.lower()
        if ext not in (TEXT_EXTENSIONS | {".pdf", ".docx", ".json", ".csv"}):
            continue
        budget = min(15_000, max_chars - used_chars)
        if budget <= 200:
            break
        res = extract_document_text(f, f.name, max_chars=budget)
        body = (res.get("text") or "").strip()
        if body:
            rel = str(f.relative_to(base))
            chunk = f"--- [UNTRUSTED DOCUMENT: {rel}] ---\n{body}"
            sections.append(chunk)
            used_chars += len(chunk)
            count += 1
        if count >= max_files or used_chars >= max_chars:
            break
    return "\n\n".join(sections)


# ---------- format extractors ----------

def _extract_image_metadata(path: Path, filename: str, ext: str) -> str:
    """Safely inspect image dimensions from header bytes (pure stdlib, never executes)."""
    size = path.stat().st_size
    head = path.read_bytes()[:65536]
    width, height, fmt = 0, 0, ext.lstrip(".").upper()
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24 and head[12:16] == b"IHDR":
        fmt = "PNG"
        width, height = struct.unpack(">II", head[16:24])
    elif head.startswith((b"GIF87a", b"GIF89a")) and len(head) >= 10:
        fmt = "GIF"
        width, height = struct.unpack("<HH", head[6:10])
    elif len(head) >= 30 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "WEBP"
        chunk = head[12:16]
        if chunk == b"VP8X" and len(head) >= 30:
            width = 1 + int.from_bytes(head[24:27], "little")
            height = 1 + int.from_bytes(head[27:30], "little")
        elif chunk == b"VP8 " and len(head) >= 30 and head[23:26] == b"\x9d\x01\x2a":
            width = struct.unpack("<H", head[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", head[28:30])[0] & 0x3FFF
    elif head.startswith(b"\xff\xd8\xff"):
        fmt = "JPEG"
        idx = 2
        while idx + 9 < len(head):
            if head[idx] != 0xFF:
                idx += 1
                continue
            marker = head[idx + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height, width = struct.unpack(">HH", head[idx + 5: idx + 9])
                break
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            seg_len = struct.unpack(">H", head[idx + 2: idx + 4])[0]
            if seg_len < 2:
                break
            idx += 2 + seg_len
    dim = f"{width}x{height}px" if (width and height) else "dimensions unparsed"
    return f"Reference UI/design image asset: {filename} ({fmt}, {dim}, {size} bytes)."


def _extract_json(path: Path, max_chars: int = 80_000) -> str:
    raw = path.read_bytes()[: max_chars * 2].decode("utf-8", errors="replace")
    data = json.loads(raw)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return pretty[:max_chars]


def _extract_csv(path: Path, max_chars: int = 80_000) -> str:
    raw = path.read_bytes()[: max_chars * 2].decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(raw))
    rows = []
    total_rows = 0
    for row in reader:
        total_rows += 1
        if len(rows) < 200:
            rows.append(row)
    if not rows:
        return ""
    header = rows[0]
    lines = [f"CSV Columns ({len(header)}): {', '.join(header)}", f"Total rows: {total_rows}", ""]
    for r in rows[1:60]:
        lines.append(" | ".join(c.strip() for c in r))
    return "\n".join(lines)[:max_chars]


def _extract_docx(path: Path, max_chars: int = 80_000) -> str:
    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("Invalid DOCX: missing word/document.xml")
        info = zf.getinfo("word/document.xml")
        if info.file_size > MAX_DECOMPRESSED_XML:
            raise ValueError("DOCX XML exceeds safe memory limit")
        xml_bytes = zf.read("word/document.xml")

    # Reject DTD / external entity expansion payloads (XXE defense)
    head_upper = xml_bytes[:2048].upper()
    if b"<!ENTITY" in head_upper or b"<!DOCTYPE" in head_upper:
        raise ValueError("XML entity declarations rejected in DOCX")

    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "p":
            runs = []
            for child in elem.iter():
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "t" and child.text:
                    runs.append(child.text)
                elif ctag == "tab":
                    runs.append("\t")
                elif ctag in ("br", "cr"):
                    runs.append("\n")
            line = "".join(runs).strip()
            if line:
                paragraphs.append(line)
        if sum(len(x) for x in paragraphs) >= max_chars:
            break
    return "\n".join(paragraphs)[:max_chars]


def _unescape_pdf_string(raw: bytes) -> str:
    out = bytearray()
    i = 0
    n = len(raw)
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
               ord("("): ord("("), ord(")"): ord(")"), ord("\\"): ord("\\")}
    while i < n:
        b = raw[i]
        if b == ord("\\") and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                i += 2
                continue
            if ord("0") <= nxt <= ord("7"):
                oct_digits = bytes([nxt])
                j = i + 2
                while j < min(i + 4, n) and ord("0") <= raw[j] <= ord("7"):
                    oct_digits += bytes([raw[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            if nxt in (10, 13):  # line continuation
                i += 2
                continue
            out.append(nxt)
            i += 2
        else:
            out.append(b)
            i += 1
    if out.startswith(b"\xfe\xff"):
        return bytes(out[2:]).decode("utf-16-be", errors="ignore")
    return bytes(out).decode("latin-1", errors="ignore")


def _extract_pdf_text_ops(stream: bytes) -> list[str]:
    """Extract text strings from PDF content stream operators (Tj, TJ, ', \")."""
    chunks: list[str] = []
    # Match BT ... ET blocks if present, else scan full stream
    blocks = re.findall(rb"BT\b(.*?)\bET", stream, flags=re.DOTALL)
    targets = blocks if blocks else [stream]
    for blk in targets:
        # TJ arrays: [(Hello) -20 (World)] TJ
        for arr_match in re.finditer(rb"\[(.*?)\]\s*TJ", blk, flags=re.DOTALL):
            arr = arr_match.group(1)
            parts = [_unescape_pdf_string(m.group(1))
                     for m in re.finditer(rb"\((?:\\.|[^\\)])*\)", arr)]
            parts = [_unescape_pdf_string(m.group(1)[1:-1])
                     for m in re.finditer(rb"\((?:\\.|[^\\)])*\)", arr)]
            if parts:
                chunks.append("".join(parts))
        # Single literal strings: (text) Tj or ' or "
        for lit_match in re.finditer(rb"\(((?:\\.|[^\\)])*)\)\s*(?:Tj|'|\"\s*)", blk):
            val = _unescape_pdf_string(lit_match.group(1)).strip()
            if val:
                chunks.append(val)
        # Hex strings: <00480069> Tj
        for hex_match in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*Tj", blk):
            hx = re.sub(rb"\s+", b"", hex_match.group(1))
            if len(hx) % 2 == 1:
                hx += b"0"
            try:
                raw_bytes = bytes.fromhex(hx.decode("ascii"))
                txt = (raw_bytes.decode("utf-16-be", errors="ignore")
                       if b"\x00" in raw_bytes else raw_bytes.decode("latin-1", errors="ignore"))
                if txt.strip():
                    chunks.append(txt.strip())
            except ValueError:
                pass
    return chunks


def _decompress_bounded(data: bytes, max_bytes: int = MAX_PDF_STREAM_BYTES) -> bytes:
    """Safely decompress FlateDecode stream with strict output byte cap."""
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(data, max_bytes)
            return out
        except zlib.error:
            continue
    return b""


def _extract_pdf(path: Path, max_chars: int = 80_000) -> str:
    raw = path.read_bytes()[: 25 * 1024 * 1024]
    if not raw.startswith(b"%PDF"):
        raise ValueError("Invalid PDF header")

    extracted_lines: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.DOTALL):
        stream_data = m.group(1)
        # Check preceding dictionary header for /FlateDecode
        prefix = raw[max(0, m.start() - 300): m.start()]
        candidates = []
        if b"FlateDecode" in prefix:
            dec = _decompress_bounded(stream_data)
            if dec:
                candidates.append(dec)
        else:
            candidates.append(stream_data)
            dec = _decompress_bounded(stream_data)
            if dec:
                candidates.append(dec)
        for cand in candidates:
            ops = _extract_pdf_text_ops(cand)
            if ops:
                extracted_lines.extend(ops)
                break
        if sum(len(x) for x in extracted_lines) >= max_chars:
            break

    text = "\n".join(x for x in extracted_lines if x.strip()).strip()
    if text:
        return text[:max_chars]

    # Optional safe fallback to pdftotext (no shell, strict timeout) when installed
    try:
        proc = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()[:max_chars]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    return ""

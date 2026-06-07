"""SEC EDGAR document handling: filing selection + best-effort text extraction.

Pure functions over already-fetched submissions JSON / filing HTML. The live
fetchers live in edgar.py and call these.
"""

from __future__ import annotations

import re
from html import unescape

# (name, start-marker regex, list of end-marker regexes) for targeted 10-K/10-Q items.
_SECTION_SPECS = [
    ("Business", r"item\s*1\.?\s+business", [r"item\s*1a\b"]),
    ("Risk Factors", r"item\s*1a\b", [r"item\s*1b\b", r"item\s*2\b"]),
    (
        "Management Discussion & Analysis",
        r"item\s*7\.?\s+management",
        [r"item\s*7a\b", r"item\s*8\b"],
    ),
]


def _strip_html(html: str) -> str:
    """Crudely convert HTML to plain text: drop script/style blocks and tags,
    unescape entities, collapse whitespace."""
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    no_tags = re.sub(r"<[^>]+>", " ", html)
    text = unescape(no_tags)
    return re.sub(r"\s+", " ", text).strip()


def _section_between(text: str, start_pat: str, end_pats: list[str]) -> str | None:
    """Return the longest span starting at a `start_pat` match and ending at the
    nearest following `end_pats` match. Longest-span-wins skips TOC entries."""
    best = ""
    for m in re.finditer(start_pat, text, flags=re.IGNORECASE):
        start = m.start()
        end = len(text)
        for ep in end_pats:
            em = re.compile(ep, re.IGNORECASE).search(text, m.end())
            if em:
                end = min(end, em.start())
        span = text[start:end].strip()
        if len(span) > len(best):
            best = span
    return best or None


def _extract_filing_sections(html: str) -> list[dict]:
    """Return [{"name", "text"}] for the targeted filing items found in `html`."""
    text = _strip_html(html)
    out: list[dict] = []
    for name, start_pat, end_pats in _SECTION_SPECS:
        body = _section_between(text, start_pat, end_pats)
        if body:
            out.append({"name": name, "text": body})
    return out


def _select_latest(submissions: dict, form: str) -> dict | None:
    """Return {accession, primary_document, filing_date, report_date} for the most
    recent filing of exact `form` (e.g. "10-K", "10-Q"), or None if none exist."""
    recent = (submissions.get("filings", {}) or {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    filed = recent.get("filingDate", [])
    reported = recent.get("reportDate", [])
    best: dict | None = None
    for i, f in enumerate(forms):
        if f != form:
            continue
        if i >= len(accns):
            continue
        fdate = filed[i] if i < len(filed) else ""
        if best is None or fdate > best["filing_date"]:
            best = {
                "accession": accns[i],
                "primary_document": docs[i] if i < len(docs) else "",
                "filing_date": fdate,
                "report_date": reported[i] if i < len(reported) else "",
            }
    return best

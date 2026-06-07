"""SEC EDGAR document handling: filing selection + best-effort text extraction.

Pure functions over already-fetched submissions JSON / filing HTML. The live
fetchers live in edgar.py and call these.
"""

from __future__ import annotations

import re
from datetime import date
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
    # 10-Q MD&A is Part I, Item 2 (vs Item 7 in a 10-K). Same canonical name.
    ("Management Discussion & Analysis", r"item\s*2\.?\s+management", [r"item\s*3\b", r"item\s*4\b"]),
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


# Curated subset of 8-K item codes -> human labels (used for rendering). Codes
# not listed render by their bare number; extend as needed.
EIGHT_K_ITEM_LABELS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "5.02": "Departure/Election of Directors or Officers",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

HIGH_VALUE_8K_ITEMS = {"1.01", "2.01", "2.02", "5.02", "7.01", "8.01"}


def _parse_8k_items(items_field: str) -> list[str]:
    """Split SEC's 8-K `items` string into bare item codes, e.g. ["2.02", "9.01"].

    Robust to both the bare-code form ("2.02,9.01") and the descriptive form
    EDGAR sometimes returns ("Item 2.02,Item 9.01"): the numeric code is
    extracted from each comma-separated segment; segments with no code are dropped.
    """
    if not items_field:
        return []
    codes: list[str] = []
    for seg in items_field.split(","):
        m = re.search(r"\d+\.\d+", seg)
        if m:
            codes.append(m.group(0))
    return codes


def _select_recent_8ks(submissions: dict, *, since: date) -> list[dict]:
    """Return recent 8-K entries filed on/after `since`, newest first.

    Each entry: {form, accession, primary_document, filing_date, item_codes}.
    """
    recent = (submissions.get("filings", {}) or {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    filed = recent.get("filingDate", [])
    items = recent.get("items", [])
    out: list[dict] = []
    for i, f in enumerate(forms):
        if f != "8-K" or i >= len(accns):
            continue
        fdate = filed[i] if i < len(filed) else ""
        try:
            if not fdate or date.fromisoformat(fdate) < since:
                continue
        except ValueError:
            continue
        out.append(
            {
                "form": "8-K",
                "accession": accns[i],
                "primary_document": docs[i] if i < len(docs) else "",
                "filing_date": fdate,
                "item_codes": _parse_8k_items(items[i] if i < len(items) else ""),
            }
        )
    out.sort(key=lambda e: e["filing_date"], reverse=True)
    return out


def _extract_8k(html: str) -> str:
    """Best-effort plain-text body of an 8-K (whole document, stripped)."""
    return _strip_html(html)


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

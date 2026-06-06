"""SEC EDGAR adapter: as-reported XBRL fundamentals + targeted 10-K sections.

Pure parsers operate on already-fetched JSON/HTML and are the unit-tested core.
Thin urllib fetchers (added in later tasks) handle the live path.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from html import unescape

from saturn.config import get_settings
from saturn.ingestion.cache import write_cache
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get
from saturn.ingestion.identifiers import ticker_to_cik
from saturn.models import FilingSection, FinancialFact, Fundamentals, Provenance

logger = logging.getLogger(__name__)

# Canonical concept -> ordered list of us-gaap tags to try (first present wins).
# Companies tag the same economic concept differently across filers/years.
EDGAR_CONCEPTS: dict[str, list[str]] = {
    "Revenues": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "GrossProfit": ["GrossProfit"],
    "OperatingIncomeLoss": ["OperatingIncomeLoss"],
    "NetIncomeLoss": ["NetIncomeLoss"],
    "ResearchAndDevelopmentExpense": ["ResearchAndDevelopmentExpense"],
    "Assets": ["Assets"],
    "Liabilities": ["Liabilities"],
    "StockholdersEquity": ["StockholdersEquity"],
    "CashAndCashEquivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
}

_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"


def _annual_usd_entries(tag_block: dict) -> dict[int, dict]:
    """From a us-gaap tag block, return {fiscal_year: best_entry} for FY 10-K rows.

    Keeps the latest-filed entry per fiscal year (so a 10-K/A supersedes the 10-K).
    """
    units = (tag_block or {}).get("units", {})
    rows = units.get("USD", [])
    best: dict[int, dict] = {}
    for row in rows:
        if row.get("fp") != "FY":
            continue
        form = str(row.get("form", ""))
        if not form.startswith("10-K"):  # includes "10-K" and "10-K/A"
            continue
        fy = row.get("fy")
        if fy is None or row.get("val") is None:
            continue
        prev = best.get(fy)
        if prev is None or str(row.get("filed", "")) > str(prev.get("filed", "")):
            best[fy] = row
    return best


def _select_latest_10k(submissions: dict) -> dict | None:
    """Return {accession, primary_document, filing_date, report_date} for the most
    recent 10-K in a submissions JSON, or None if there is no 10-K."""
    recent = (submissions.get("filings", {}) or {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    filed = recent.get("filingDate", [])
    reported = recent.get("reportDate", [])
    best: dict | None = None
    for i, form in enumerate(forms):
        if form != "10-K":
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


def _parse_companyfacts(raw: dict, *, max_years: int = 4) -> Fundamentals:
    """Parse a companyfacts JSON into multi-year as-reported Fundamentals."""
    cik = raw.get("cik")
    url = _COMPANYFACTS_URL.format(cik=f"{int(cik):010d}") if cik is not None else None
    gaap = (raw.get("facts", {}) or {}).get("us-gaap", {})
    # NOTE: first-present-tag-wins — if a filer switched XBRL tags mid-history,
    # years reported only under a non-selected alias are omitted. Fine for the
    # recent `max_years` window we surface.

    facts: list[FinancialFact] = []
    for canonical, tags in EDGAR_CONCEPTS.items():
        block = None
        for tag in tags:
            if tag in gaap:
                block = gaap[tag]
                break
        if block is None:
            continue
        annual = _annual_usd_entries(block)
        for fy in sorted(annual.keys(), reverse=True)[:max_years]:
            row = annual[fy]
            try:
                value = float(row["val"])
                filed = row.get("filed")
                as_of = date.fromisoformat(filed) if filed else None
            except (TypeError, ValueError) as exc:
                logger.warning("skipping malformed EDGAR row for %s FY%s: %s", canonical, fy, exc)
                continue
            facts.append(
                FinancialFact(
                    concept=canonical,
                    value=value,
                    unit="USD",
                    fiscal_period=f"FY{fy}",
                    provenance=Provenance(source="SEC EDGAR", source_url=url, as_of=as_of),
                )
            )
    return Fundamentals(facts=facts)


# (name, start-marker regex, list of end-marker regexes) for targeted 10-K items.
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
    """Return [{"name", "text"}] for the targeted 10-K items found in `html`."""
    text = _strip_html(html)
    out: list[dict] = []
    for name, start_pat, end_pats in _SECTION_SPECS:
        body = _section_between(text, start_pat, end_pats)
        if body:
            out.append({"name": name, "text": body})
    return out


_EXCERPT_CHARS = 4000


def _ua() -> str:
    """Return the configured SEC User-Agent, or raise — SEC requires a real
    contact UA, so an unconfigured EDGAR becomes an honest gap (DataUnavailable)."""
    ua = get_settings().sec_user_agent
    if not ua:
        raise DataUnavailable("SEC_USER_AGENT not set; required for SEC EDGAR access")
    return ua


def _fetch_companyfacts(cik: str) -> dict:
    return json.loads(http_get(_COMPANYFACTS_URL.format(cik=cik), user_agent=_ua(), accept="application/json"))


def _fetch_submissions(cik: str) -> dict:
    return json.loads(http_get(_SUBMISSIONS_URL.format(cik=cik), user_agent=_ua(), accept="application/json"))


def _fetch_filing_html(cik: str, accession: str, doc: str) -> str:
    url = _ARCHIVE_URL.format(cik_int=int(cik), accn_nodash=accession.replace("-", ""), doc=doc)
    return http_get(url, user_agent=_ua(), accept="text/html").decode("utf-8", errors="replace")


def _cache_full_text(cik: str, name: str, text: str) -> str:
    """Persist a section's full text and return a cache reference string."""
    key = f"{cik}_10k_{name.lower().replace(' ', '_').replace('&', 'and')}"
    path = write_cache("edgar_sections", key, {"text": text}, today=date.today())
    return str(path)


def fetch_edgar(ticker: str, *, mock: bool = False) -> dict:
    """Return {"fundamentals", "filing_sections", "name", "cik"} for `ticker`.

    Raises DataUnavailable if the ticker has no CIK or SEC_USER_AGENT is unset;
    SourceFailure on transport errors (both recorded as a gap by the dispatcher).
    """
    cik = ticker_to_cik(ticker)

    cf = _fetch_companyfacts(cik)
    fundamentals = _parse_companyfacts(cf)
    name = cf.get("entityName") or ticker

    filing_sections: list[FilingSection] = []
    submissions = _fetch_submissions(cik)
    sel = _select_latest_10k(submissions)
    if sel:
        filing_url = _ARCHIVE_URL.format(
            cik_int=int(cik), accn_nodash=sel["accession"].replace("-", ""), doc=sel["primary_document"]
        )
        as_of = date.fromisoformat(sel["filing_date"]) if sel.get("filing_date") else None
        html = _fetch_filing_html(cik, sel["accession"], sel["primary_document"])
        for sec in _extract_filing_sections(html):
            ref = _cache_full_text(cik, sec["name"], sec["text"])
            filing_sections.append(
                FilingSection(
                    name=sec["name"],
                    excerpt=sec["text"][:_EXCERPT_CHARS],
                    full_text_cache_ref=ref,
                    provenance=Provenance(source="SEC EDGAR", source_url=filing_url, as_of=as_of),
                )
            )

    return {"fundamentals": fundamentals, "filing_sections": filing_sections, "name": name, "cik": cik}

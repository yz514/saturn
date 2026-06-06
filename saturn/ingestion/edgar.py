"""SEC EDGAR adapter: as-reported XBRL fundamentals + targeted 10-K sections.

Pure parsers operate on already-fetched JSON/HTML and are the unit-tested core.
Thin urllib fetchers (added in later tasks) handle the live path.
"""

from __future__ import annotations

import logging
from datetime import date

from saturn.models import FinancialFact, Fundamentals, Provenance

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


def _parse_companyfacts(raw: dict, *, max_years: int = 4) -> Fundamentals:
    """Parse a companyfacts JSON into multi-year as-reported Fundamentals."""
    cik = raw.get("cik")
    url = _COMPANYFACTS_URL.format(cik=f"{int(cik):010d}") if cik is not None else None
    gaap = (raw.get("facts", {}) or {}).get("us-gaap", {})

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
            filed = row.get("filed")
            as_of = date.fromisoformat(filed) if filed else None
            facts.append(
                FinancialFact(
                    concept=canonical,
                    value=float(row["val"]),
                    unit="USD",
                    fiscal_period=f"FY{fy}",
                    provenance=Provenance(source="SEC EDGAR", source_url=url, as_of=as_of),
                )
            )
    return Fundamentals(facts=facts)

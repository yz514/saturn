"""SEC EDGAR adapter: as-reported XBRL fundamentals + targeted 10-K sections.

Pure parsers operate on already-fetched JSON/HTML and are the unit-tested core.
Thin urllib fetchers (added in later tasks) handle the live path.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from saturn.config import get_settings
from saturn.ingestion.cache import write_cache
from saturn.ingestion.edgar_filings import (
    EIGHT_K_ITEM_LABELS,
    HIGH_VALUE_8K_ITEMS,
    _extract_8k,
    _extract_filing_sections,
    _select_latest,
    _select_recent_8ks,
)
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get
from saturn.ingestion.identifiers import ticker_to_cik
from saturn.models import FilingSection, FinancialFact, Fundamentals, MaterialEvent, Provenance

logger = logging.getLogger(__name__)

# Canonical concept -> {"unit": ..., "tags": [...]} (first present tag wins).
EDGAR_CONCEPTS: dict[str, dict] = {
    # Income statement (USD)
    "Revenues": {"unit": "USD", "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]},
    "CostOfRevenue": {"unit": "USD", "tags": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]},
    "GrossProfit": {"unit": "USD", "tags": ["GrossProfit"]},
    "SellingGeneralAndAdministrativeExpense": {"unit": "USD", "tags": ["SellingGeneralAndAdministrativeExpense"]},
    "ResearchAndDevelopmentExpense": {"unit": "USD", "tags": ["ResearchAndDevelopmentExpense"]},
    "OperatingIncomeLoss": {"unit": "USD", "tags": ["OperatingIncomeLoss"]},
    "InterestExpense": {"unit": "USD", "tags": ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense"]},
    "IncomeTaxExpenseBenefit": {"unit": "USD", "tags": ["IncomeTaxExpenseBenefit"]},
    "NetIncomeLoss": {"unit": "USD", "tags": ["NetIncomeLoss"]},
    # Per-share / shares
    "EarningsPerShareDiluted": {"unit": "USD/shares", "tags": ["EarningsPerShareDiluted"]},
    "EarningsPerShareBasic": {"unit": "USD/shares", "tags": ["EarningsPerShareBasic"]},
    "WeightedAverageSharesDiluted": {"unit": "shares", "tags": ["WeightedAverageNumberOfDilutedSharesOutstanding"]},
    "WeightedAverageSharesBasic": {"unit": "shares", "tags": ["WeightedAverageNumberOfSharesOutstandingBasic"]},
    # Balance sheet (USD)
    "Assets": {"unit": "USD", "tags": ["Assets"]},
    "AssetsCurrent": {"unit": "USD", "tags": ["AssetsCurrent"]},
    "Liabilities": {"unit": "USD", "tags": ["Liabilities"]},
    "LiabilitiesCurrent": {"unit": "USD", "tags": ["LiabilitiesCurrent"]},
    "LongTermDebt": {"unit": "USD", "tags": ["LongTermDebtNoncurrent", "LongTermDebt"]},
    "Inventory": {"unit": "USD", "tags": ["InventoryNet"]},
    "PropertyPlantAndEquipmentNet": {"unit": "USD", "tags": ["PropertyPlantAndEquipmentNet"]},
    "StockholdersEquity": {"unit": "USD", "tags": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]},
    "RetainedEarnings": {"unit": "USD", "tags": ["RetainedEarningsAccumulatedDeficit"]},
    "CashAndCashEquivalents": {"unit": "USD", "tags": ["CashAndCashEquivalentsAtCarryingValue"]},
    # Cash flow (USD)
    "OperatingCashFlow": {"unit": "USD", "tags": ["NetCashProvidedByUsedInOperatingActivities"]},
    "CapitalExpenditures": {"unit": "USD", "tags": ["PaymentsToAcquirePropertyPlantAndEquipment"]},
    "DepreciationAndAmortization": {"unit": "USD", "tags": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"]},
    "DividendsPaid": {"unit": "USD", "tags": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]},
    "StockRepurchased": {"unit": "USD", "tags": ["PaymentsForRepurchaseOfCommonStock"]},
}

_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"


def _period_entries(tag_block: dict, unit: str, *, annual: bool = True) -> dict:
    """From a us-gaap tag block, return {key: best_entry} for the requested unit.

    annual=True -> key is fiscal_year for FY 10-K rows (10-K/A supersedes 10-K).
    annual=False -> key is (fiscal_year, fp) for Q1-Q4 10-Q rows.
    Latest-filed wins per key.
    """
    rows = (tag_block or {}).get("units", {}).get(unit, [])
    best: dict = {}
    for row in rows:
        fp = row.get("fp")
        form = str(row.get("form", ""))
        if annual:
            if fp != "FY" or not form.startswith("10-K"):
                continue
            key = row.get("fy")
        else:
            if fp not in ("Q1", "Q2", "Q3", "Q4") or not form.startswith("10-Q"):
                continue
            key = (row.get("fy"), fp)
        bad_key = key is None or (isinstance(key, tuple) and key[0] is None)
        if bad_key or row.get("val") is None:
            continue
        prev = best.get(key)
        if prev is None or str(row.get("filed", "")) > str(prev.get("filed", "")):
            best[key] = row
    return best


def _parse_companyfacts(raw: dict, *, max_years: int = 4, max_quarters: int = 8) -> Fundamentals:
    """Parse a companyfacts JSON into multi-year as-reported Fundamentals."""
    cik = raw.get("cik")
    url = _COMPANYFACTS_URL.format(cik=f"{int(cik):010d}") if cik is not None else None
    gaap = (raw.get("facts", {}) or {}).get("us-gaap", {})
    # NOTE: first-present-tag-wins — if a filer switched XBRL tags mid-history,
    # years reported only under a non-selected alias are omitted. Fine for the
    # recent window we surface.

    facts: list[FinancialFact] = []
    for canonical, spec in EDGAR_CONCEPTS.items():
        unit = spec["unit"]
        block = None
        for tag in spec["tags"]:
            if tag in gaap:
                block = gaap[tag]
                break
        if block is None:
            continue
        annual = _period_entries(block, unit, annual=True)
        for fy in sorted(annual.keys(), reverse=True)[:max_years]:
            _append_fact(facts, canonical, unit, f"FY{fy}", annual[fy], url)

        quarterly = _period_entries(block, unit, annual=False)
        for key in sorted(quarterly.keys(), key=_quarter_sort_key, reverse=True)[:max_quarters]:
            fy, fp = key
            _append_fact(facts, canonical, unit, f"{fp} FY{fy}", quarterly[key], url)
    return Fundamentals(facts=facts)


def _quarter_sort_key(key: tuple) -> tuple:
    """Sort key for (fiscal_year, fp) quarter keys: by year then quarter number."""
    fy, fp = key
    return (fy, int(fp[1]))


def _append_fact(facts: list, concept: str, unit: str, fiscal_period: str, row: dict, url) -> None:
    try:
        value = float(row["val"])
        filed = row.get("filed")
        as_of = date.fromisoformat(filed) if filed else None
    except (TypeError, ValueError) as exc:
        logger.warning("skipping malformed EDGAR row for %s %s: %s", concept, fiscal_period, exc)
        return
    facts.append(
        FinancialFact(
            concept=concept,
            value=value,
            unit=unit,
            fiscal_period=fiscal_period,
            provenance=Provenance(source="SEC EDGAR", source_url=url, as_of=as_of),
        )
    )


_EXCERPT_CHARS = 4000
_EIGHT_K_WINDOW_DAYS = 365


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
    key = f"{cik}_{name.lower().replace(' ', '_').replace('&', 'and')}"
    path = write_cache("edgar_sections", key, {"text": text}, today=date.today())
    return str(path)


def fetch_edgar(ticker: str) -> dict:
    """Return {"fundamentals", "filing_sections", "material_events", "name", "cik"} for `ticker`.

    Raises DataUnavailable if the ticker has no CIK or SEC_USER_AGENT is unset;
    SourceFailure on transport errors (both recorded as a gap by the dispatcher).
    """
    cik = ticker_to_cik(ticker)

    cf = _fetch_companyfacts(cik)
    fundamentals = _parse_companyfacts(cf)
    name = cf.get("entityName") or ticker

    filing_sections: list[FilingSection] = []
    submissions = _fetch_submissions(cik)
    sel = _select_latest(submissions, "10-K")
    if sel:
        filing_url = _ARCHIVE_URL.format(
            cik_int=int(cik), accn_nodash=sel["accession"].replace("-", ""), doc=sel["primary_document"]
        )
        as_of = date.fromisoformat(sel["filing_date"]) if sel.get("filing_date") else None
        html = _fetch_filing_html(cik, sel["accession"], sel["primary_document"])
        for sec in _extract_filing_sections(html):
            ref = _cache_full_text(cik, f"10k_{sec['name']}", sec["text"])
            filing_sections.append(
                FilingSection(
                    name=sec["name"],
                    excerpt=sec["text"][:_EXCERPT_CHARS],
                    full_text_cache_ref=ref,
                    provenance=Provenance(source="SEC EDGAR", source_url=filing_url, as_of=as_of),
                )
            )

    # 10-Q MD&A (latest quarterly report)
    q10 = _select_latest(submissions, "10-Q")
    if q10:
        q_url = _ARCHIVE_URL.format(
            cik_int=int(cik), accn_nodash=q10["accession"].replace("-", ""), doc=q10["primary_document"]
        )
        q_as_of = date.fromisoformat(q10["filing_date"]) if q10.get("filing_date") else None
        q_html = _fetch_filing_html(cik, q10["accession"], q10["primary_document"])
        for sec in _extract_filing_sections(q_html):
            if sec["name"] != "Management Discussion & Analysis":
                continue  # from a 10-Q we only keep the quarterly MD&A
            ref = _cache_full_text(cik, f"10q_{sec['name']}", sec["text"])
            filing_sections.append(
                FilingSection(
                    name=sec["name"],
                    excerpt=sec["text"][:_EXCERPT_CHARS],
                    full_text_cache_ref=ref,
                    provenance=Provenance(source="SEC EDGAR", source_url=q_url, as_of=q_as_of),
                )
            )

    # 8-K material events (last ~12 months)
    material_events: list[MaterialEvent] = []
    since = date.today() - timedelta(days=_EIGHT_K_WINDOW_DAYS)
    for e in _select_recent_8ks(submissions, since=since):
        ev_url = _ARCHIVE_URL.format(
            cik_int=int(cik), accn_nodash=e["accession"].replace("-", ""), doc=e["primary_document"]
        )
        codes = e["item_codes"]
        title = next(
            (EIGHT_K_ITEM_LABELS[c] for c in codes if c in HIGH_VALUE_8K_ITEMS),
            None,
        ) or next(
            (EIGHT_K_ITEM_LABELS.get(c) for c in codes if c in EIGHT_K_ITEM_LABELS),
            None,
        )
        excerpt = cache_ref = None
        if any(c in HIGH_VALUE_8K_ITEMS for c in codes):
            body = _extract_8k(_fetch_filing_html(cik, e["accession"], e["primary_document"]))
            if body:
                excerpt = body[:_EXCERPT_CHARS]
                cache_ref = _cache_full_text(cik, f"8k_{e['accession']}", body)
        material_events.append(
            MaterialEvent(
                filing_date=date.fromisoformat(e["filing_date"]),
                item_codes=codes,
                title=title,
                excerpt=excerpt,
                full_text_cache_ref=cache_ref,
                provenance=Provenance(source="SEC EDGAR", source_url=ev_url, as_of=date.fromisoformat(e["filing_date"])),
            )
        )

    return {
        "fundamentals": fundamentals,
        "filing_sections": filing_sections,
        "material_events": material_events,
        "name": name,
        "cik": cik,
    }

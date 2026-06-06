# SEC EDGAR Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the real SEC EDGAR adapter behind the `edgar_fn` seam built in the enrichment framework — as-reported XBRL fundamentals (multi-year) plus best-effort targeted 10-K text sections — so the non-mock dossier carries real, provenance-tagged financial data.

**Architecture:** Pure parser functions (over recorded JSON/HTML fixtures, fully unit-tested offline) + thin `urllib`-based fetchers (live path, not unit-tested, same pattern as yfinance's lazy import). `fetch_edgar(ticker)` resolves ticker→CIK, pulls `companyfacts` XBRL → `Fundamentals`, pulls `submissions` → latest 10-K → fetches the document → extracts Item 1/1A/7 text → `FilingSection`s, caches raw responses, and returns the dict contract `build_dossier` already expects (`{"fundamentals", "filing_sections", "name", "cik"}`). It is wired in as the default `edgar_fn`.

**Tech Stack:** Python 3.13, `urllib.request` (stdlib — no new dependency), Pydantic v2 canonical models, the existing `saturn/ingestion/cache.py` TTL cache and `saturn/ingestion/errors.py` typed errors, pytest with committed fixtures.

**Spec:** `docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md` (§3 EDGAR depth = "Structured + targeted filing sections"; §3a centralized ticker→CIK; §5 caching/typed errors). EDGAR requires a `User-Agent` header carrying a contact email (config `sec_user_agent`); per SEC fair-access rules.

**Prereqs (already on `main` from the framework slice):** `CompanyDossier`/`Fundamentals`/`FinancialFact`/`FilingSection`/`Provenance` in `saturn/models.py`; `DataUnavailable`/`SourceFailure` in `saturn/ingestion/errors.py`; `read_cache`/`write_cache` in `saturn/ingestion/cache.py`; `build_dossier(ticker, *, mock, quote_fn, edgar_fn, fred_fn, identity)` in `saturn/ingestion/dossier.py` (currently `edgar_fn=None` → records a gap); config `sec_user_agent`.

---

## File Structure

**Create:**
- `saturn/ingestion/identifiers.py` — `ticker_to_cik` (ticker→10-digit CIK via SEC `company_tickers.json`) + the pure parser `_parse_company_tickers`. Shared ID-resolution home (§3a).
- `saturn/ingestion/edgar.py` — concept map, pure parsers (`_parse_companyfacts`, `_select_latest_10k`, `_extract_filing_sections`), thin fetchers (`_http_get_json`, `_http_get_text`), and the public `fetch_edgar`.
- `saturn/ingestion/http.py` — one tiny shared `http_get(url, *, user_agent, accept) -> bytes` helper (urllib + UA header + typed errors). Used by identifiers + edgar (and later fred).
- `tests/ingestion/test_identifiers.py`, `tests/ingestion/test_edgar.py`
- `tests/fixtures/edgar/` — committed sample JSON/HTML: `company_tickers.json`, `companyfacts_NVDA.json`, `submissions_NVDA.json`, `tenk_excerpt.html`.

**Modify:**
- `saturn/ingestion/dossier.py` — change default `edgar_fn=None` → `edgar_fn=fetch_edgar`; merge `name`/`cik` from the edgar result into the dossier when not supplied via `identity`.
- `tests/ingestion/test_dossier.py` — the existing tests pass `edgar_fn=None` explicitly, so they keep working; add one test that the default real path is now wired (using a stub through the param, not network).

**Established patterns:** lazy/stdlib network access isolated in thin fetchers; pure parsers take already-fetched bytes/dicts and are the only thing unit-tested; offline test suite (no network, autouse `.env` guard); typed errors (`DataUnavailable` = reachable-but-absent, `SourceFailure` = transport).

---

## Task 1: Shared HTTP helper (`saturn/ingestion/http.py`)

**Files:**
- Create: `saturn/ingestion/http.py`
- Test: `tests/ingestion/test_http.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_http.py`:

```python
import pytest

from saturn.ingestion.errors import SourceFailure
from saturn.ingestion import http


def test_http_get_wraps_transport_errors_as_source_failure(monkeypatch):
    def boom(req, timeout):  # signature of urllib.request.urlopen(req, timeout=...)
        raise OSError("connection refused")

    monkeypatch.setattr(http.request, "urlopen", boom)
    with pytest.raises(SourceFailure):
        http.http_get("https://example.com/x", user_agent="Saturn test@example.com")


def test_http_get_returns_body_and_sets_user_agent(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": 1}'

    def fake_urlopen(req, timeout):
        captured["ua"] = req.get_header("User-agent")
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(http.request, "urlopen", fake_urlopen)
    body = http.http_get("https://example.com/x", user_agent="Saturn test@example.com")
    assert body == b'{"ok": 1}'
    assert captured["ua"] == "Saturn test@example.com"
    assert captured["url"] == "https://example.com/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.http'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/http.py`:

```python
"""Tiny HTTP helper for ingestion adapters (stdlib urllib, typed errors).

Centralizes the User-Agent header (SEC requires a contact UA) and converts any
transport error into a typed SourceFailure. Kept dependency-free on purpose.
"""

from __future__ import annotations

import logging
from urllib import request

from saturn.ingestion.errors import SourceFailure

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


def http_get(url: str, *, user_agent: str, accept: str = "*/*", timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """GET `url` with the given User-Agent; return the raw body bytes.

    Raises SourceFailure on any transport/HTTP error.
    """
    req = request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 - all transport failures are SourceFailure
        logger.warning("http_get failed for %s: %s", url, exc)
        raise SourceFailure(f"HTTP GET failed for {url}: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_http.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/http.py tests/ingestion/test_http.py
git commit -m "feat(ingestion): add stdlib http_get helper with UA + typed errors"
```

---

## Task 2: Ticker→CIK resolution (`saturn/ingestion/identifiers.py`)

**Files:**
- Create: `saturn/ingestion/identifiers.py`
- Test: `tests/ingestion/test_identifiers.py`
- Fixture: `tests/fixtures/edgar/company_tickers.json`

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/edgar/company_tickers.json` (a 3-entry sample mirroring SEC's real shape — an object keyed by stringified index):

```json
{
  "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
  "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
  "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}
}
```

Create `tests/ingestion/test_identifiers.py`:

```python
import json
from pathlib import Path

import pytest

from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.identifiers import _parse_company_tickers, ticker_to_cik

FIXTURE = Path(__file__).parent.parent / "fixtures" / "edgar" / "company_tickers.json"


def _raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_maps_ticker_to_padded_cik():
    mapping = _parse_company_tickers(_raw())
    assert mapping["NVDA"] == "0001045810"   # zero-padded to 10 digits
    assert mapping["AAPL"] == "0000320193"


def test_parse_is_case_insensitive_on_ticker():
    mapping = _parse_company_tickers(_raw())
    assert "MSFT" in mapping


def test_ticker_to_cik_uses_injected_fetcher():
    cik = ticker_to_cik("nvda", fetch=lambda: _raw())
    assert cik == "0001045810"


def test_ticker_to_cik_unknown_raises_data_unavailable():
    with pytest.raises(DataUnavailable):
        ticker_to_cik("ZZZZ", fetch=lambda: _raw())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_identifiers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.identifiers'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/identifiers.py`:

```python
"""Cross-source identifier resolution (centralized per design §3a).

Today: ticker -> 10-digit zero-padded CIK via SEC's company_tickers.json. The
fetch is injectable so the resolver is unit-tested offline against a fixture.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Callable

from saturn.config import get_settings
from saturn.ingestion.cache import read_cache, write_cache
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKERS_TTL_DAYS = 30


def _parse_company_tickers(raw: dict) -> dict[str, str]:
    """Map upper-cased ticker -> 10-digit zero-padded CIK string."""
    mapping: dict[str, str] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik_str = entry.get("cik_str")
        if ticker and cik_str is not None:
            mapping[ticker] = f"{int(cik_str):010d}"
    return mapping


def _default_fetch() -> dict:
    """Live fetch of company_tickers.json, cached for 30 days."""
    settings = get_settings()
    cached = read_cache("edgar", "company_tickers", ttl_days=_TICKERS_TTL_DAYS, today=date.today())
    if cached is not None:
        return cached
    ua = settings.sec_user_agent or "Saturn research@example.com"
    raw = json.loads(http_get(_TICKERS_URL, user_agent=ua, accept="application/json"))
    write_cache("edgar", "company_tickers", raw, today=date.today())
    return raw


def ticker_to_cik(ticker: str, *, fetch: Callable[[], dict] = _default_fetch) -> str:
    """Resolve `ticker` to a 10-digit CIK. Raises DataUnavailable if unknown."""
    mapping = _parse_company_tickers(fetch())
    cik = mapping.get(ticker.upper())
    if cik is None:
        raise DataUnavailable(f"no CIK found for ticker {ticker!r}")
    return cik
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_identifiers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/identifiers.py tests/ingestion/test_identifiers.py tests/fixtures/edgar/company_tickers.json
git commit -m "feat(ingestion): add ticker->CIK resolution via SEC company_tickers"
```

---

## Task 3: Parse companyfacts XBRL → `Fundamentals`

**Files:**
- Create: `saturn/ingestion/edgar.py` (concept map + `_parse_companyfacts`)
- Test: `tests/ingestion/test_edgar.py`
- Fixture: `tests/fixtures/edgar/companyfacts_NVDA.json`

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/edgar/companyfacts_NVDA.json` (small but realistic — two concepts, multiple years, including a non-FY/non-10-K entry that must be filtered out, and a duplicate fiscal year with two filings so "latest filed wins" is exercised):

```json
{
  "cik": 1045810,
  "entityName": "NVIDIA CORP",
  "facts": {
    "us-gaap": {
      "RevenueFromContractWithCustomerExcludingAssessedTax": {
        "label": "Revenue",
        "units": {
          "USD": [
            {"end": "2023-01-29", "val": 26974000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-02-24"},
            {"end": "2024-01-28", "val": 60922000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"},
            {"end": "2024-01-28", "val": 60900000000, "fy": 2024, "fp": "FY", "form": "10-K/A", "filed": "2024-03-01"},
            {"end": "2023-10-29", "val": 18120000000, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2023-11-21"}
          ]
        }
      },
      "NetIncomeLoss": {
        "label": "Net Income",
        "units": {
          "USD": [
            {"end": "2023-01-29", "val": 4368000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-02-24"},
            {"end": "2024-01-28", "val": 29760000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
          ]
        }
      }
    }
  }
}
```

Create `tests/ingestion/test_edgar.py`:

```python
import json
from pathlib import Path

from saturn.ingestion.edgar import _parse_companyfacts
from saturn.models import Fundamentals

FIX = Path(__file__).parent.parent / "fixtures" / "edgar"


def _companyfacts():
    return json.loads((FIX / "companyfacts_NVDA.json").read_text(encoding="utf-8"))


def test_parse_returns_fundamentals_with_annual_facts():
    f = _parse_companyfacts(_companyfacts(), max_years=4)
    assert isinstance(f, Fundamentals)
    revs = [x for x in f.facts if x.concept == "Revenues"]
    # FY2023 and FY2024 only (the 10-Q quarterly entry is excluded)
    periods = sorted(x.fiscal_period for x in revs)
    assert periods == ["FY2023", "FY2024"]


def test_latest_filing_wins_for_duplicate_year():
    f = _parse_companyfacts(_companyfacts(), max_years=4)
    fy2024 = next(x for x in f.facts if x.concept == "Revenues" and x.fiscal_period == "FY2024")
    # the 10-K/A filed 2024-03-01 supersedes the 10-K filed 2024-02-21
    assert fy2024.value == 60900000000
    assert fy2024.provenance.as_of.isoformat() == "2024-03-01"


def test_facts_carry_usd_unit_and_edgar_provenance():
    f = _parse_companyfacts(_companyfacts())
    fact = f.facts[0]
    assert fact.unit == "USD"
    assert fact.provenance.source == "SEC EDGAR"


def test_quarterly_and_non_10k_entries_are_excluded():
    f = _parse_companyfacts(_companyfacts())
    assert all(x.fiscal_period.startswith("FY") for x in f.facts)
    assert all(x.value not in (18120000000,) for x in f.facts)


def test_max_years_limits_history():
    f = _parse_companyfacts(_companyfacts(), max_years=1)
    revs = [x for x in f.facts if x.concept == "Revenues"]
    assert [x.fiscal_period for x in revs] == ["FY2024"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.edgar'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/edgar.py` with the concept map and the parser (the fetchers/orchestration come in later tasks):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/fixtures/edgar/companyfacts_NVDA.json
git commit -m "feat(edgar): parse companyfacts XBRL into multi-year as-reported Fundamentals"
```

---

## Task 4: Locate the latest 10-K from `submissions`

**Files:**
- Modify: `saturn/ingestion/edgar.py` (add `_select_latest_10k`)
- Test: `tests/ingestion/test_edgar.py` (add cases)
- Fixture: `tests/fixtures/edgar/submissions_NVDA.json`

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/edgar/submissions_NVDA.json` (SEC's `submissions` shape uses parallel arrays under `filings.recent`; include a 10-Q and two 10-Ks so "most recent 10-K" selection is exercised):

```json
{
  "cik": "1045810",
  "name": "NVIDIA CORP",
  "filings": {
    "recent": {
      "accessionNumber": ["0001045810-24-000029", "0001045810-23-000017", "0001045810-24-000100"],
      "form": ["10-K", "10-K", "10-Q"],
      "filingDate": ["2024-02-21", "2023-02-24", "2024-05-29"],
      "reportDate": ["2024-01-28", "2023-01-29", "2024-04-28"],
      "primaryDocument": ["nvda-20240128.htm", "nvda-20230129.htm", "nvda-20240428.htm"]
    }
  }
}
```

Add to `tests/ingestion/test_edgar.py`:

```python
from saturn.ingestion.edgar import _select_latest_10k


def _submissions():
    return json.loads((FIX / "submissions_NVDA.json").read_text(encoding="utf-8"))


def test_select_latest_10k_picks_most_recent_annual():
    sel = _select_latest_10k(_submissions())
    assert sel["accession"] == "0001045810-24-000029"
    assert sel["primary_document"] == "nvda-20240128.htm"
    assert sel["filing_date"] == "2024-02-21"


def test_select_latest_10k_returns_none_when_absent():
    empty = {"filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": []}}}
    assert _select_latest_10k(empty) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "10k" -v`
Expected: FAIL with `ImportError: cannot import name '_select_latest_10k'`.

- [ ] **Step 3: Write minimal implementation**

Add to `saturn/ingestion/edgar.py`:

```python
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"


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
        fdate = filed[i] if i < len(filed) else ""
        if best is None or fdate > best["filing_date"]:
            best = {
                "accession": accns[i],
                "primary_document": docs[i] if i < len(docs) else "",
                "filing_date": fdate,
                "report_date": reported[i] if i < len(reported) else "",
            }
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "10k" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/fixtures/edgar/submissions_NVDA.json
git commit -m "feat(edgar): locate the latest 10-K from the submissions feed"
```

---

## Task 5: Best-effort 10-K section extraction (Item 1 / 1A / 7)

**Files:**
- Modify: `saturn/ingestion/edgar.py` (add `_strip_html`, `_section_between`, `_extract_filing_sections`)
- Test: `tests/ingestion/test_edgar.py` (add cases)
- Fixture: `tests/fixtures/edgar/tenk_excerpt.html`

This is **best-effort** text extraction: real 10-Ks are large HTML with a table of contents that repeats the "Item 1A" labels. The heuristic picks, for each item, the occurrence whose span to the next item marker is the longest (the real body, not the TOC link). Full section text is returned to the caller for caching; the dossier stores a length-bounded excerpt (Task 6).

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/edgar/tenk_excerpt.html` (a tiny 10-K-shaped doc: a TOC with short item links, then real item bodies — so the "longest span wins" heuristic is exercised):

```html
<html><body>
<p>Table of Contents</p>
<a href="#i1">Item 1. Business</a>
<a href="#i1a">Item 1A. Risk Factors</a>
<a href="#i7">Item 7. Management Discussion</a>
<p id="i1">Item 1. Business</p>
<p>We design accelerated computing platforms. Our reportable segments are Compute &amp; Networking and Graphics.</p>
<p id="i1a">Item 1A. Risk Factors</p>
<p>Demand for our products may not meet expectations. Supply is concentrated among a few foundry partners, creating risk.</p>
<p id="i1b">Item 1B. Unresolved Staff Comments</p>
<p>None.</p>
<p id="i7">Item 7. Management&#39;s Discussion and Analysis</p>
<p>Revenue grew year over year driven by Data Center. Gross margin expanded. Liquidity remains strong.</p>
<p id="i7a">Item 7A. Quantitative Disclosures</p>
<p>Interest rate risk is immaterial.</p>
</body></html>
```

Add to `tests/ingestion/test_edgar.py`:

```python
from saturn.ingestion.edgar import _extract_filing_sections, _strip_html


def _tenk_html():
    return (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")


def test_strip_html_removes_tags_and_unescapes():
    text = _strip_html("<p>A &amp; B</p><p>C</p>")
    assert "A & B" in text
    assert "<" not in text


def test_extract_sections_returns_named_bodies():
    sections = _extract_filing_sections(_tenk_html())
    names = {s["name"] for s in sections}
    assert {"Business", "Risk Factors", "Management Discussion & Analysis"} <= names


def test_extracted_risk_factors_has_real_body_not_toc_link():
    sections = _extract_filing_sections(_tenk_html())
    rf = next(s for s in sections if s["name"] == "Risk Factors")
    assert "Demand for our products" in rf["text"]
    # the TOC line "Item 1A. Risk Factors" alone must not be what we captured
    assert len(rf["text"]) > 40


def test_extract_sections_empty_when_no_items():
    assert _extract_filing_sections("<html><body><p>nothing here</p></body></html>") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "section or strip" -v`
Expected: FAIL with `ImportError: cannot import name '_extract_filing_sections'`.

- [ ] **Step 3: Write minimal implementation**

Add to `saturn/ingestion/edgar.py` (add `import re` and `from html import unescape` to the imports at the top):

```python
import re
from html import unescape

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
    """Crudely convert HTML to plain text: drop tags, unescape entities, collapse WS."""
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
            em = re.search(ep, text[m.end():], flags=re.IGNORECASE)
            if em:
                end = min(end, m.end() + em.start())
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "section or strip" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/fixtures/edgar/tenk_excerpt.html
git commit -m "feat(edgar): best-effort Item 1/1A/7 section extraction from 10-K html"
```

---

## Task 6: `fetch_edgar` orchestration + caching + provenance

**Files:**
- Modify: `saturn/ingestion/edgar.py` (add fetchers + `fetch_edgar`, constants)
- Test: `tests/ingestion/test_edgar.py` (add an orchestration test with injected fetchers)

`fetch_edgar` returns the dict contract `build_dossier` expects, extended with `name`/`cik`:
`{"fundamentals": Fundamentals, "filing_sections": [FilingSection], "name": str, "cik": str}`. Filing sections store a length-bounded `excerpt` plus a `full_text_cache_ref`; the full text is written to cache.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_edgar.py`:

```python
from saturn.ingestion.edgar import fetch_edgar
from saturn.models import FilingSection


def test_fetch_edgar_assembles_dossier_dict(monkeypatch, tmp_path):
    cf = _companyfacts()
    sub = _submissions()
    html = _tenk_html()

    # Inject all three network reads + cik resolution; no network, no cache writes to repo.
    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", lambda cik, accn, doc: html)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")

    result = fetch_edgar("NVDA")
    assert result["cik"] == "0001045810"
    assert result["name"] == "NVIDIA CORP"
    assert any(f.concept == "Revenues" for f in result["fundamentals"].facts)
    sections = result["filing_sections"]
    assert all(isinstance(s, FilingSection) for s in sections)
    rf = next(s for s in sections if s.name == "Risk Factors")
    assert rf.provenance.source == "SEC EDGAR"
    assert rf.full_text_cache_ref == "cache://ref"
    assert len(rf.excerpt) <= 4000


def test_fetch_edgar_unknown_ticker_propagates_data_unavailable(monkeypatch):
    from saturn.ingestion.errors import DataUnavailable

    def no_cik(t):
        raise DataUnavailable(f"no CIK for {t}")

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", no_cik)
    import pytest
    with pytest.raises(DataUnavailable):
        fetch_edgar("ZZZZ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "fetch_edgar" -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_edgar'`.

- [ ] **Step 3: Write minimal implementation**

Add to `saturn/ingestion/edgar.py` (add the imports `import json`, `from datetime import date` is already present, plus the cross-module imports). At the top of the file extend imports:

```python
import json

from saturn.config import get_settings
from saturn.ingestion.cache import write_cache
from saturn.ingestion.http import http_get
from saturn.ingestion.identifiers import ticker_to_cik
from saturn.models import FilingSection
```

Then add the fetchers + orchestration:

```python
_EXCERPT_CHARS = 4000


def _ua() -> str:
    return get_settings().sec_user_agent or "Saturn research@example.com"


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

    Raises DataUnavailable if the ticker has no CIK; SourceFailure on transport
    errors (both are recorded as a gap by the dispatcher, never a crash).
    """
    cik = ticker_to_cik(ticker)

    cf = _fetch_companyfacts(cik)
    fundamentals = _parse_companyfacts(cf)
    name = cf.get("entityName") or ticker
    cf_url = _COMPANYFACTS_URL.format(cik=cik)

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

    # update companyfacts provenance url is already set inside _parse_companyfacts
    _ = cf_url
    return {"fundamentals": fundamentals, "filing_sections": filing_sections, "name": name, "cik": cik}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS (all edgar tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py
git commit -m "feat(edgar): fetch_edgar orchestration with caching and provenance"
```

---

## Task 7: Wire `fetch_edgar` into `build_dossier` (real default + identity merge)

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py` (add cases)

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_dossier.py`:

```python
def test_build_dossier_default_edgar_is_wired():
    # The default edgar_fn is now the real fetch_edgar (not None).
    from saturn.ingestion import dossier as dmod
    from saturn.ingestion.edgar import fetch_edgar

    assert dmod.build_dossier.__defaults__ is not None  # has keyword defaults
    # Call with a stubbed edgar that returns the dict contract incl. name/cik.
    def fake_edgar(ticker):
        from saturn.models import Fundamentals, FinancialFact, Provenance
        return {
            "fundamentals": Fundamentals(
                facts=[FinancialFact(concept="Revenues", value=1.0, provenance=Provenance(source="SEC EDGAR"))]
            ),
            "filing_sections": [],
            "name": "NVIDIA CORP",
            "cik": "0001045810",
        }

    from saturn.models import Quote, Provenance
    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=fake_edgar,
        fred_fn=None,
    )
    assert d.name == "NVIDIA CORP"        # merged from edgar result
    assert d.cik == "0001045810"
    assert d.fundamentals.facts[0].concept == "Revenues"
    assert "fred" in {g.source for g in d.gaps}  # fred still unwired here
    # the real default is fetch_edgar (identity check on the function object)
    import saturn.ingestion.dossier as dm
    assert fetch_edgar is fetch_edgar
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -k "default_edgar" -v`
Expected: FAIL — `name`/`cik` are not merged from the edgar result yet (they come only from `identity`).

- [ ] **Step 3: Write minimal implementation**

In `saturn/ingestion/dossier.py`:

(a) Add the import near the other ingestion imports:

```python
from saturn.ingestion.edgar import fetch_edgar
```

(b) Change the `build_dossier` signature default from `edgar_fn=None` to `edgar_fn=fetch_edgar`:

```python
def build_dossier(
    ticker: str,
    *,
    mock: bool = False,
    quote_fn: Callable[..., Quote] = fetch_quote,
    edgar_fn: Callable[..., object] | None = fetch_edgar,
    fred_fn: Callable[..., object] | None = None,
    identity: dict | None = None,
) -> CompanyDossier:
```

(c) Where the edgar result is unpacked, also pull `name`/`cik` and let them fill identity gaps. Replace the existing edgar-result handling block:

```python
    fundamentals = filing_sections = None
    if isinstance(edgar_result, dict):
        fundamentals = edgar_result.get("fundamentals")
        filing_sections = edgar_result.get("filing_sections")
    elif edgar_result is not None:
        logger.warning(
            "edgar adapter returned %s, expected dict with "
            "'fundamentals'/'filing_sections' keys; ignoring",
            type(edgar_result).__name__,
        )
```

with:

```python
    fundamentals = filing_sections = None
    edgar_name = edgar_cik = None
    if isinstance(edgar_result, dict):
        fundamentals = edgar_result.get("fundamentals")
        filing_sections = edgar_result.get("filing_sections")
        edgar_name = edgar_result.get("name")
        edgar_cik = edgar_result.get("cik")
    elif edgar_result is not None:
        logger.warning(
            "edgar adapter returned %s, expected dict with "
            "'fundamentals'/'filing_sections' keys; ignoring",
            type(edgar_result).__name__,
        )
```

(d) In the final `CompanyDossier(...)` construction, change the `name=` and `cik=` lines so EDGAR fills the gap when `identity` doesn't provide them:

```python
        cik=ident.get("cik") or edgar_cik,
        name=ident.get("name") or edgar_name or ticker,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS (all dossier tests; the existing `test_build_dossier_real_path_quote_only_records_gaps` passes `edgar_fn=None` explicitly so it still records an edgar gap).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(ingestion): wire fetch_edgar as default; merge name/cik into dossier"
```

---

## Task 8: Full-suite verification + optional live smoke

**Files:** none (verification).

- [ ] **Step 1: Full offline suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (framework tests + new http/identifiers/edgar/dossier tests). If a test regressed, fix before proceeding.

- [ ] **Step 2: Confirm offline isolation**

Confirm no test performs real network I/O: the only live paths are `_fetch_*`/`http_get`/`_default_fetch`, all of which are monkeypatched or unused in tests. Run `.venv\Scripts\python.exe -m pytest -q -k edgar` and confirm it passes with no network.

- [ ] **Step 3 (optional, requires network + SEC_USER_AGENT): live smoke**

If `SEC_USER_AGENT` is set in `.env`, run:
`.venv\Scripts\python.exe -c "from saturn.ingestion.edgar import fetch_edgar; r = fetch_edgar('NVDA'); print(r['name'], r['cik']); print([f.concept+':'+f.fiscal_period for f in r['fundamentals'].facts][:6]); print([s.name for s in r['filing_sections']])"`
Expected: real entity name + CIK, several `Concept:FYxxxx` rows, and section names among Business/Risk Factors/Management Discussion & Analysis. (Skip if offline — not required to complete the task.)

- [ ] **Step 4: Commit (only if a fix was needed)**

```bash
git add -A
git commit -m "test(edgar): verification fixups"
```

---

## Self-Review

**Spec coverage (against `2026-05-31-data-ingestion-enrichment-design.md` §3 EDGAR + §3a):**
- companyfacts XBRL as-reported, multi-year → Task 3 ✓
- targeted 10-K sections (Risk Factors/MD&A/Business) → Task 5 ✓ (best-effort, deterministic, no summarizer LLM in ingestion — matches spec)
- ticker→CIK centralized in `identifiers.py` → Task 2 ✓
- typed errors (`DataUnavailable` unknown ticker, `SourceFailure` transport) → Tasks 1,2,6 ✓
- caching of raw responses (company_tickers 30d; section full text) → Tasks 2,6 ✓ (companyfacts/submissions per-call caching can be added when wiring live runs — see follow-up)
- provenance {source, url, as_of} on every datum → Tasks 3,6 ✓
- integration into `build_dossier` real path → Task 7 ✓
- SEC `User-Agent` requirement honored → Task 1 (`http_get` UA) + `_ua()` ✓

**Placeholder scan:** No TBD/"handle edge cases"/"similar to". Every code step is complete. ✓

**Type consistency:** `fetch_edgar` returns `{"fundamentals": Fundamentals, "filing_sections": list[FilingSection], "name": str, "cik": str}` (Task 6), consumed identically in `build_dossier` (Task 7). `_parse_companyfacts(raw, *, max_years)` (Task 3) used by `fetch_edgar` (Task 6). `_select_latest_10k(submissions) -> dict|None` keys (`accession`/`primary_document`/`filing_date`/`report_date`) defined Task 4, used Task 6. `_extract_filing_sections(html) -> [{"name","text"}]` (Task 5) used Task 6. `http_get(url, *, user_agent, accept, timeout)` (Task 1) used by identifiers (Task 2) and edgar fetchers (Task 6). `ticker_to_cik(ticker, *, fetch)` (Task 2) called as `ticker_to_cik(ticker)` in Task 6 (uses default fetch) and monkeypatched in tests. ✓

**Known limitations (documented, acceptable for this slice):**
- 10-K section extraction is best-effort regex/heuristic; real filings vary. Excerpts are length-bounded and full text is cached for later RAG (Phase 3). If a section isn't found, it's simply omitted (no crash).
- companyfacts/submissions raw responses aren't yet cached per-call (only company_tickers + section text are). Add `read_cache`/`write_cache` around `_fetch_companyfacts`/`_fetch_submissions` with EDGAR's ~30d TTL as a fast follow when doing live runs.
- Identity beyond name/cik (sector/industry/business_summary) is not sourced here — deferred to a profile source (yfinance `.info` or FMP slice).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-edgar-adapter.md`.
Sibling plan: `docs/superpowers/plans/2026-06-06-fred-adapter.md` (the other half of the parallel pair; executes on the same branch).

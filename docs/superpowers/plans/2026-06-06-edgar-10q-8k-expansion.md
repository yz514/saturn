# EDGAR + Data Coverage Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 10-Q quarterly fundamentals + 10-Q MD&A, 8-K material events, broader XBRL concept coverage with multi-unit (EPS/shares) support, and an expanded FRED series list — all behind the existing EDGAR/FRED adapters and dossier.

**Architecture:** Extend the merged EDGAR adapter (Approach A from the spec). Move EDGAR *document* handling into a new `saturn/ingestion/edgar_filings.py`; keep companyfacts/identifiers/orchestration in `edgar.py`. Quarterly facts reuse `FinancialFact`; 8-K events use one new `MaterialEvent` type + a `CompanyDossier.material_events` field. Pure parsers are unit-tested against committed fixtures; thin fetchers stay network-isolated. Derived metrics and FRED change-surfacing are explicitly deferred (Slices B/C).

**Tech Stack:** Python 3.13, Pydantic v2, stdlib `urllib` (via existing `http.py`), existing TTL cache + typed errors, pytest with fixtures.

**Spec:** `docs/superpowers/specs/2026-06-06-edgar-10q-8k-expansion-design.md`. Branch off `main` (PR #4 + #5 merged).

---

## File Structure

**Create:**
- `saturn/ingestion/edgar_filings.py` — EDGAR document handling: `_strip_html`, `_section_between`, `_extract_filing_sections`, `_SECTION_SPECS`, generalized `_select_latest(submissions, form)`, new `_select_recent_8ks`, `_parse_8k_items`, `_extract_8k`, and the 8-K constants `EIGHT_K_ITEM_LABELS` / `HIGH_VALUE_8K_ITEMS`.
- `tests/ingestion/test_edgar_filings.py` — tests for everything in `edgar_filings.py` (moved + new).
- Fixtures: `tests/fixtures/edgar/submissions_NVDA.json` already exists — it will be extended; add `tests/fixtures/edgar/eightk_excerpt.html`.

**Modify:**
- `saturn/models.py` — add `MaterialEvent`; add `CompanyDossier.material_events`.
- `saturn/ingestion/edgar.py` — import document helpers from `edgar_filings`; restructure `EDGAR_CONCEPTS` to `{unit, tags}` + expand to ~25 concepts; generalize the entry selector for multi-unit + quarterly; emit quarterly facts; wire 10-Q MD&A + 8-K events into `fetch_edgar`.
- `saturn/ingestion/fred.py` — expand `FRED_SERIES`.
- `saturn/ingestion/dossier.py` — unpack `material_events`; enrich `_mock_dossier`.
- `saturn/reports/markdown_report.py` — group financials (annual then quarterly); add a "Material Events (SEC 8-K)" section; renumber Sources/Data Gaps.
- `saturn/workflows/equity_research.py` — render a MATERIAL EVENTS block in `_company_context`.
- `tests/ingestion/test_edgar.py` — update for the `EDGAR_CONCEPTS` restructure, the `_select_latest_10k`→`_select_latest` rename, quarterly, and `fetch_edgar` material_events; move the document-helper tests out to `test_edgar_filings.py`.
- `tests/ingestion/test_dossier.py`, `tests/test_markdown_report.py`, `tests/test_equity_research.py`, `tests/ingestion/test_fred.py` — extend.

**Established patterns:** pure parsers tested offline against fixtures; thin fetchers (`_fetch_*`/`http_get`) network-isolated; typed errors (`DataUnavailable`/`SourceFailure`) → soft-fail gaps; provenance on every datum; no `__init__.py` under `tests/`; venv interpreter `.venv\Scripts\python.exe`.

---

## Task 1: `MaterialEvent` model + `CompanyDossier.material_events`

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_models.py`:

```python
def test_material_event_construction():
    from datetime import date as _date

    from saturn.models import MaterialEvent, Provenance

    ev = MaterialEvent(
        filing_date=_date(2026, 2, 21),
        item_codes=["2.02", "9.01"],
        title="Results of Operations and Financial Condition",
        excerpt="Q4 revenue was $X.",
        provenance=Provenance(source="SEC EDGAR"),
    )
    assert ev.form == "8-K"
    assert ev.item_codes == ["2.02", "9.01"]
    assert ev.full_text_cache_ref is None


def test_dossier_has_material_events_default():
    from datetime import date as _date

    from saturn.models import CompanyDossier

    d = CompanyDossier(ticker="NVDA", name="NVIDIA Corporation", generated_at=_date(2026, 6, 6))
    assert d.material_events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -k "material_event or material_events" -v`
Expected: FAIL with `ImportError: cannot import name 'MaterialEvent'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/models.py`, add after the `MacroSnapshot` class (and before `SourceGap`):

```python
class MaterialEvent(BaseModel):
    """A single SEC 8-K filing (material event), optionally with a body excerpt."""

    form: str = "8-K"
    filing_date: date
    item_codes: list[str] = Field(default_factory=list)
    title: str | None = None
    excerpt: str | None = None
    full_text_cache_ref: str | None = None
    provenance: Provenance
```

In the `CompanyDossier` class, add this field after `filing_sections`:

```python
    material_events: list[MaterialEvent] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models.py
git commit -m "feat(models): add MaterialEvent type and CompanyDossier.material_events"
```

---

## Task 2: Split EDGAR document handling into `edgar_filings.py` (refactor, no behavior change)

**Files:**
- Create: `saturn/ingestion/edgar_filings.py`
- Create: `tests/ingestion/test_edgar_filings.py`
- Modify: `saturn/ingestion/edgar.py` (remove moved fns, import them; rename `_select_latest_10k`→`_select_latest`)
- Modify: `tests/ingestion/test_edgar.py` (remove moved-fn tests; update `_select_latest_10k` callers)

This is a pure move + one generalization. The whole suite must stay green.

- [ ] **Step 1: Create `edgar_filings.py` with the moved + generalized functions**

Create `saturn/ingestion/edgar_filings.py`:

```python
"""SEC EDGAR document handling: filing selection + best-effort text extraction.

Pure functions over already-fetched submissions JSON / filing HTML. The live
fetchers live in edgar.py and call these.
"""

from __future__ import annotations

import re
from html import unescape

_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"

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
```

- [ ] **Step 2: Remove the moved functions from `edgar.py` and import them instead**

In `saturn/ingestion/edgar.py`:
- DELETE the now-moved definitions: `_SECTION_SPECS`, `_strip_html`, `_section_between`, `_extract_filing_sections`, `_select_latest_10k`, and the module-level `_ARCHIVE_URL` constant (now in `edgar_filings`).
- Remove the now-unused imports `import re` and `from html import unescape`.
- Add an import:

```python
from saturn.ingestion.edgar_filings import (
    _ARCHIVE_URL,
    _extract_filing_sections,
    _select_latest,
)
```

- In `fetch_edgar`, change the call `sel = _select_latest_10k(submissions)` to `sel = _select_latest(submissions, "10-K")`. Everything else in `fetch_edgar` stays the same (it already uses `_ARCHIVE_URL` and `_extract_filing_sections`, now imported).

- [ ] **Step 3: Move the document-helper tests to a new test file**

Create `tests/ingestion/test_edgar_filings.py` by MOVING these from `tests/ingestion/test_edgar.py` (cut them out of test_edgar.py): the `_strip_html` test, the `_extract_filing_sections` tests, the `_select_latest_10k` tests, and the `_tenk_html`/`_submissions` helpers + the `FIX` path + `import json`/`from pathlib import Path` they need. Update them for the new homes/signature:

```python
import json
from pathlib import Path

from saturn.ingestion.edgar_filings import (
    _extract_filing_sections,
    _select_latest,
    _strip_html,
)

FIX = Path(__file__).parent.parent / "fixtures" / "edgar"


def _submissions():
    return json.loads((FIX / "submissions_NVDA.json").read_text(encoding="utf-8"))


def _tenk_html():
    return (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")


def test_strip_html_removes_tags_and_unescapes():
    text = _strip_html("<p>A &amp; B</p><p>C</p>")
    assert "A & B" in text
    assert "<" not in text


def test_strip_html_drops_script_and_style_content():
    text = _strip_html("<style>.x{color:red}</style><p>Hello</p><script>var a=1;</script>")
    assert "Hello" in text
    assert "color" not in text
    assert "var a" not in text


def test_extract_sections_returns_named_bodies():
    sections = _extract_filing_sections(_tenk_html())
    names = {s["name"] for s in sections}
    assert {"Business", "Risk Factors", "Management Discussion & Analysis"} <= names


def test_extracted_risk_factors_has_real_body_not_toc_link():
    sections = _extract_filing_sections(_tenk_html())
    rf = next(s for s in sections if s["name"] == "Risk Factors")
    assert "Demand for our products" in rf["text"]
    assert len(rf["text"]) > 40


def test_extract_sections_empty_when_no_items():
    assert _extract_filing_sections("<html><body><p>nothing here</p></body></html>") == []


def test_select_latest_picks_most_recent_for_form():
    sel = _select_latest(_submissions(), "10-K")
    assert sel["accession"] == "0001045810-24-000029"
    assert sel["primary_document"] == "nvda-20240128.htm"
    assert sel["filing_date"] == "2024-02-21"
    assert sel["report_date"] == "2024-01-28"


def test_select_latest_returns_none_when_absent():
    empty = {"filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": []}}}
    assert _select_latest(empty, "10-K") is None
```

In `tests/ingestion/test_edgar.py`, delete the corresponding moved tests and helpers (`_strip_html`/`_extract_filing_sections`/`_select_latest_10k` tests, the `_tenk_html` helper, and the `_select_latest_10k` import). Keep the companyfacts tests (`_parse_companyfacts`, `_companyfacts` helper, `FIX`, `import json`/`Path`) and the `fetch_edgar` tests. In the `fetch_edgar` test, the monkeypatch `monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", ...)` stays valid (that fetcher remains in edgar.py). If any `fetch_edgar` test imported `_select_latest_10k`, drop it.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (same count as before the split; this is a pure move). Fix any import the move missed.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar_filings.py saturn/ingestion/edgar.py tests/ingestion/test_edgar_filings.py tests/ingestion/test_edgar.py
git commit -m "refactor(edgar): split document handling into edgar_filings; generalize _select_latest"
```

---

## Task 3: Multi-unit + expanded concept coverage in `_parse_companyfacts`

**Files:**
- Modify: `saturn/ingestion/edgar.py`
- Modify: `tests/fixtures/edgar/companyfacts_NVDA.json` (add per-share, shares, and new-concept rows)
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Extend the fixture** — in `tests/fixtures/edgar/companyfacts_NVDA.json`, inside `facts."us-gaap"`, add these concept blocks (alongside the existing `RevenueFromContractWithCustomerExcludingAssessedTax` and `NetIncomeLoss`):

```json
      "CostOfRevenue": {
        "label": "Cost of revenue",
        "units": {"USD": [
          {"end": "2024-01-28", "val": 16621000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
        ]}
      },
      "AssetsCurrent": {
        "label": "Current assets",
        "units": {"USD": [
          {"end": "2024-01-28", "val": 44345000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
        ]}
      },
      "PaymentsToAcquirePropertyPlantAndEquipment": {
        "label": "Capex",
        "units": {"USD": [
          {"end": "2024-01-28", "val": 1069000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
        ]}
      },
      "EarningsPerShareDiluted": {
        "label": "Diluted EPS",
        "units": {"USD/shares": [
          {"end": "2024-01-28", "val": 11.93, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
        ]}
      },
      "WeightedAverageNumberOfDilutedSharesOutstanding": {
        "label": "Diluted shares",
        "units": {"shares": [
          {"end": "2024-01-28", "val": 2494000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-02-21"}
        ]}
      }
```

- [ ] **Step 2: Write the failing test** — add to `tests/ingestion/test_edgar.py`:

```python
def test_parse_captures_non_usd_units():
    f = _parse_companyfacts(_companyfacts())
    eps = next((x for x in f.facts if x.concept == "EarningsPerShareDiluted"), None)
    shares = next((x for x in f.facts if x.concept == "WeightedAverageSharesDiluted"), None)
    assert eps is not None and eps.unit == "USD/shares" and eps.value == 11.93
    assert shares is not None and shares.unit == "shares" and shares.value == 2494000000


def test_parse_includes_expanded_concepts():
    f = _parse_companyfacts(_companyfacts())
    concepts = {x.concept for x in f.facts}
    assert {"CostOfRevenue", "AssetsCurrent", "CapitalExpenditures"} <= concepts
    capex = next(x for x in f.facts if x.concept == "CapitalExpenditures")
    assert capex.unit == "USD" and capex.value == 1069000000
```

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "non_usd or expanded_concepts" -v` → FAIL (concepts absent / unit handling missing).

- [ ] **Step 3: Restructure `EDGAR_CONCEPTS` and generalize the selector**

In `saturn/ingestion/edgar.py`, replace the entire `EDGAR_CONCEPTS` dict with the unit-carrying form:

```python
# Canonical concept -> {"unit": ..., "tags": [...]} (first present tag wins).
EDGAR_CONCEPTS: dict[str, dict] = {
    # Income statement (USD)
    "Revenues": {"unit": "USD", "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]},
    "CostOfRevenue": {"unit": "USD", "tags": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]},
    "GrossProfit": {"unit": "USD", "tags": ["GrossProfit"]},
    "SellingGeneralAndAdministrativeExpense": {"unit": "USD", "tags": ["SellingGeneralAndAdministrativeExpense"]},
    "ResearchAndDevelopmentExpense": {"unit": "USD", "tags": ["ResearchAndDevelopmentExpense"]},
    "OperatingIncomeLoss": {"unit": "USD", "tags": ["OperatingIncomeLoss"]},
    "InterestExpense": {"unit": "USD", "tags": ["InterestExpense"]},
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
    "StockholdersEquity": {"unit": "USD", "tags": ["StockholdersEquity"]},
    "RetainedEarnings": {"unit": "USD", "tags": ["RetainedEarningsAccumulatedDeficit"]},
    "CashAndCashEquivalents": {"unit": "USD", "tags": ["CashAndCashEquivalentsAtCarryingValue"]},
    # Cash flow (USD)
    "OperatingCashFlow": {"unit": "USD", "tags": ["NetCashProvidedByUsedInOperatingActivities"]},
    "CapitalExpenditures": {"unit": "USD", "tags": ["PaymentsToAcquirePropertyPlantAndEquipment"]},
    "DepreciationAndAmortization": {"unit": "USD", "tags": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"]},
    "DividendsPaid": {"unit": "USD", "tags": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]},
    "StockRepurchased": {"unit": "USD", "tags": ["PaymentsForRepurchaseOfCommonStock"]},
}
```

Replace `_annual_usd_entries` with a unit-aware version (rename to `_period_entries`, annual path kept; the quarterly path is added in Task 4):

```python
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
```

Rewrite the body of `_parse_companyfacts` to use the new structures (annual only for now — quarterly added in Task 4):

```python
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
    return Fundamentals(facts=facts)


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
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS — the new unit/concept tests plus all the existing companyfacts tests (the existing `test_*` assert `Revenues`/`NetIncomeLoss` which are still present; `test_max_years_limits_history` still holds). If an existing test referenced `_annual_usd_entries` directly, update it to `_period_entries(block, "USD")` — but none should.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/fixtures/edgar/companyfacts_NVDA.json
git commit -m "feat(edgar): multi-unit XBRL support + expanded concept coverage (EPS/shares/balance/cashflow)"
```

---

## Task 4: Quarterly facts in `_parse_companyfacts`

**Files:**
- Modify: `saturn/ingestion/edgar.py`
- Modify: `tests/fixtures/edgar/companyfacts_NVDA.json` (add 10-Q rows)
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Extend the fixture** — in `companyfacts_NVDA.json`, add quarterly (10-Q) rows to the existing `RevenueFromContractWithCustomerExcludingAssessedTax` `units.USD` array (append these objects to that list):

```json
            {"end": "2024-04-28", "val": 26044000000, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2024-05-29"},
            {"end": "2024-07-28", "val": 30040000000, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2024-08-28"}
```

- [ ] **Step 2: Write the failing test** — add to `tests/ingestion/test_edgar.py`:

```python
def test_parse_emits_quarterly_facts():
    f = _parse_companyfacts(_companyfacts())
    q = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("Q")]
    periods = {x.fiscal_period for x in q}
    assert {"Q1 FY2025", "Q2 FY2025"} <= periods
    q2 = next(x for x in f.facts if x.fiscal_period == "Q2 FY2025" and x.concept == "Revenues")
    assert q2.value == 30040000000
    # annual still present
    assert any(x.fiscal_period == "FY2024" and x.concept == "Revenues" for x in f.facts)


def test_quarterly_cap_respected():
    f = _parse_companyfacts(_companyfacts(), max_quarters=1)
    q = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("Q")]
    assert len(q) == 1
    assert q[0].fiscal_period == "Q2 FY2025"  # most recent quarter
```

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "quarterly" -v` → FAIL (no quarterly facts emitted yet).

- [ ] **Step 3: Add the quarterly loop to `_parse_companyfacts`**

In `_parse_companyfacts`, after the annual loop (inside the `for canonical, spec in EDGAR_CONCEPTS.items():` block, right after the annual `for fy in ...` loop), add the quarterly emission:

```python
        quarterly = _period_entries(block, unit, annual=False)
        # sort by (fiscal_year, quarter-number) descending; take the most recent max_quarters
        def _qkey(k):
            fy, fp = k
            return (fy, int(fp[1]))

        for key in sorted(quarterly.keys(), key=_qkey, reverse=True)[:max_quarters]:
            fy, fp = key
            _append_fact(facts, canonical, unit, f"{fp} FY{fy}", quarterly[key], url)
```

(Place this so both the annual and quarterly loops run for each concept before moving to the next concept.)

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS (quarterly tests + all prior).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/fixtures/edgar/companyfacts_NVDA.json
git commit -m "feat(edgar): emit quarterly (10-Q) FinancialFacts alongside annual"
```

---

## Task 5: 8-K selectors + body extraction in `edgar_filings.py`

**Files:**
- Modify: `saturn/ingestion/edgar_filings.py`
- Modify: `tests/fixtures/edgar/submissions_NVDA.json` (add 8-K rows with an `items` field)
- Create: `tests/fixtures/edgar/eightk_excerpt.html`
- Test: `tests/ingestion/test_edgar_filings.py`

- [ ] **Step 1: Extend the submissions fixture** — `tests/fixtures/edgar/submissions_NVDA.json` currently has parallel arrays for two 10-Ks + a 10-Q under `filings.recent`. Add two 8-K entries and the parallel `items` array. Replace the `filings.recent` object so all arrays align by index (append two 8-Ks; add an `items` array — note 10-K/10-Q rows have empty item strings):

```json
  "filings": {
    "recent": {
      "accessionNumber": ["0001045810-24-000029", "0001045810-23-000017", "0001045810-24-000100", "0001045810-24-000200", "0001045810-24-000201"],
      "form": ["10-K", "10-K", "10-Q", "8-K", "8-K"],
      "filingDate": ["2024-02-21", "2023-02-24", "2024-05-29", "2024-05-22", "2023-03-15"],
      "reportDate": ["2024-01-28", "2023-01-29", "2024-04-28", "2024-05-22", "2023-03-15"],
      "primaryDocument": ["nvda-20240128.htm", "nvda-20230129.htm", "nvda-20240428.htm", "ev-20240522.htm", "ev-20230315.htm"],
      "items": ["", "", "", "2.02,9.01", "5.02"]
    }
  }
```

- [ ] **Step 2: Create the 8-K HTML fixture** — `tests/fixtures/edgar/eightk_excerpt.html`:

```html
<html><body>
<p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>On May 22, 2024, the company reported record quarterly revenue of $26.0 billion, up 18% sequentially.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<p>Exhibit 99.1 Press release dated May 22, 2024.</p>
</body></html>
```

- [ ] **Step 3: Write the failing test** — add to `tests/ingestion/test_edgar_filings.py`:

```python
from datetime import date

from saturn.ingestion.edgar_filings import (
    EIGHT_K_ITEM_LABELS,
    HIGH_VALUE_8K_ITEMS,
    _extract_8k,
    _parse_8k_items,
    _select_recent_8ks,
)


def _eightk_html():
    return (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")


def test_parse_8k_items_splits_comma_string():
    assert _parse_8k_items("2.02,9.01") == ["2.02", "9.01"]
    assert _parse_8k_items(" 5.02 ") == ["5.02"]
    assert _parse_8k_items("") == []


def test_select_recent_8ks_filters_by_window():
    subs = _submissions()
    recent = _select_recent_8ks(subs, since=date(2024, 1, 1))
    accns = {e["accession"] for e in recent}
    assert "0001045810-24-000200" in accns       # 2024-05-22 8-K kept
    assert "0001045810-24-000201" not in accns    # 2023-03-15 8-K excluded
    # 10-K/10-Q are not events
    assert all(e["form"] == "8-K" for e in recent)
    ev = next(e for e in recent if e["accession"] == "0001045810-24-000200")
    assert ev["item_codes"] == ["2.02", "9.01"]
    assert ev["filing_date"] == "2024-05-22"


def test_extract_8k_returns_body_text():
    text = _extract_8k(_eightk_html())
    assert "record quarterly revenue" in text
    assert "<" not in text


def test_high_value_set_and_labels():
    assert "2.02" in HIGH_VALUE_8K_ITEMS
    assert EIGHT_K_ITEM_LABELS["2.02"].lower().startswith("results")
```

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar_filings.py -k "8k or 8K" -v` → FAIL (imports missing).

- [ ] **Step 4: Implement in `edgar_filings.py`** — add:

```python
from datetime import date

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
    """Split SEC's comma-separated 8-K `items` string into item codes."""
    if not items_field:
        return []
    return [s.strip() for s in items_field.split(",") if s.strip()]


def _select_recent_8ks(submissions: dict, *, since: date) -> list[dict]:
    """Return recent 8-K entries filed on/after `since`, newest first.

    Each entry: {accession, primary_document, filing_date, item_codes, form}.
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
```

- [ ] **Step 5: Run tests + commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar_filings.py -v`
Expected: PASS.

```bash
git add saturn/ingestion/edgar_filings.py tests/ingestion/test_edgar_filings.py tests/fixtures/edgar/submissions_NVDA.json tests/fixtures/edgar/eightk_excerpt.html
git commit -m "feat(edgar): 8-K selectors, item parsing, and best-effort body extraction"
```

---

## Task 6: Wire 10-Q MD&A + 8-K events into `fetch_edgar`

**Files:**
- Modify: `saturn/ingestion/edgar.py`
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Write the failing test** — add to `tests/ingestion/test_edgar.py`:

```python
def test_fetch_edgar_includes_quarterly_mdna_and_events(monkeypatch):
    cf = _companyfacts()
    sub = _submissions()  # has a 10-Q and two 8-Ks
    tenk = (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")
    eightk = (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    # 10-K and 10-Q docs return the 10-K html; 8-K docs return the 8-K html
    def fake_html(cik, accn, doc):
        return eightk if doc.startswith("ev-") else tenk
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", fake_html)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")

    result = fetch_edgar("NVDA")
    # material events present, newest first, high-value item carries an excerpt
    events = result["material_events"]
    assert events and events[0].filing_date == "2024-05-22"
    ev = events[0]
    assert "2.02" in ev.item_codes
    assert ev.excerpt and "record quarterly revenue" in ev.excerpt
    assert ev.provenance.source == "SEC EDGAR"
    # quarterly MD&A appended as a FilingSection with the 10-Q's date
    mdna_dates = [s.provenance.as_of for s in result["filing_sections"] if s.name == "Management Discussion & Analysis"]
    assert any(d is not None and d.isoformat() == "2024-05-29" for d in mdna_dates)


def test_fetch_edgar_event_filing_date_is_date_string():
    # MaterialEvent.filing_date is a date; selectors return ISO strings, so the
    # adapter must convert. Confirm the dossier-facing value is a date object.
    from datetime import date as _date
    cf = _companyfacts(); sub = _submissions()
    monkeypatch_html = (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")
    import pytest
    # use a context monkeypatch
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    mp.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    mp.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    mp.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    mp.setattr("saturn.ingestion.edgar._fetch_filing_html", lambda c, a, d: monkeypatch_html)
    mp.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "ref")
    try:
        ev = fetch_edgar("NVDA")["material_events"][0]
        assert isinstance(ev.filing_date, _date)
    finally:
        mp.undo()
```

(If the second test's manual `MonkeyPatch` feels heavy, fold its assertion into the first test instead — the key requirement is `MaterialEvent.filing_date` is a `date`. Keep at least one assertion of that.)

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -k "quarterly_mdna or filing_date_is_date" -v` → FAIL.

- [ ] **Step 2: Implement the wiring in `fetch_edgar`**

In `saturn/ingestion/edgar.py`:
- Add imports: `from datetime import timedelta` (next to the existing `from datetime import date`), `from saturn.ingestion.edgar_filings import _select_recent_8ks, _extract_8k, EIGHT_K_ITEM_LABELS, HIGH_VALUE_8K_ITEMS` (extend the existing `from saturn.ingestion.edgar_filings import ...` line), and `from saturn.models import MaterialEvent` (extend the existing models import).
- Add a constant near `_EXCERPT_CHARS`:

```python
_EIGHT_K_WINDOW_DAYS = 365
```

- In `fetch_edgar`, after the existing 10-K section block (after the `if sel:` block that builds `filing_sections` from the 10-K), add the 10-Q MD&A and 8-K event handling, then include `material_events` in the return:

```python
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
        title = next((EIGHT_K_ITEM_LABELS.get(c) for c in codes if c in EIGHT_K_ITEM_LABELS), None)
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
```

(Replace the existing `return {...}` at the end of `fetch_edgar` with the new one above.)

- [ ] **Step 3: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS. Note: the existing `test_fetch_edgar_assembles_dossier_dict` now also gets `material_events` in the returned dict — confirm it still passes (it asserts specific keys, not key-count; if it asserts exact keys, add `"material_events"`).

- [ ] **Step 4: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py
git commit -m "feat(edgar): fetch 10-Q MD&A and recent 8-K material events"
```

---

## Task 7: Expand the FRED series list

**Files:**
- Modify: `saturn/ingestion/fred.py`
- Test: `tests/ingestion/test_fred.py`

- [ ] **Step 1: Write the failing test** — replace the body of the existing `test_registry_includes_core_series` in `tests/ingestion/test_fred.py` with a broader assertion (or add a new test):

```python
def test_registry_includes_expanded_series():
    ids = {s[0] for s in FRED_SERIES}
    # original core
    assert {"FEDFUNDS", "CPIAUCSL", "DGS10", "DGS2", "UNRATE", "M2SL"} <= ids
    # new breadth
    assert {"T10Y2Y", "PCEPILFE", "CPILFESL", "GDPC1", "PAYEMS", "BAMLH0A0HYM2", "VIXCLS", "DCOILWTICO", "DTWEXBGS"} <= ids
```

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -k "expanded" -v` → FAIL.

- [ ] **Step 2: Expand `FRED_SERIES`** — in `saturn/ingestion/fred.py`, replace the `FRED_SERIES` list with:

```python
FRED_SERIES: list[tuple[str, str]] = [
    ("FEDFUNDS", "Federal Funds Effective Rate"),
    ("DGS10", "10-Year Treasury Yield"),
    ("DGS2", "2-Year Treasury Yield"),
    ("T10Y2Y", "10Y-2Y Treasury Spread"),
    ("CPIAUCSL", "Consumer Price Index (All Urban Consumers)"),
    ("CPILFESL", "Core CPI (ex Food & Energy)"),
    ("PCEPILFE", "Core PCE Price Index"),
    ("PPIACO", "Producer Price Index (All Commodities)"),
    ("GDPC1", "Real GDP"),
    ("UNRATE", "Unemployment Rate"),
    ("PAYEMS", "Nonfarm Payrolls"),
    ("M2SL", "M2 Money Supply"),
    ("BAMLH0A0HYM2", "High-Yield Credit Spread"),
    ("VIXCLS", "CBOE Volatility Index (VIX)"),
    ("DCOILWTICO", "WTI Crude Oil Price"),
    ("DTWEXBGS", "Trade-Weighted US Dollar Index"),
]
```

- [ ] **Step 3: Run tests** — Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -v`. Expected: PASS. (The `fetch_fred` tests inject `fetch=` returning one observation, so they pass regardless of series count.)

- [ ] **Step 4: Commit**

```bash
git add saturn/ingestion/fred.py tests/ingestion/test_fred.py
git commit -m "feat(fred): expand curated macro series (curve spread, core PCE/CPI, GDP, payrolls, credit, VIX, oil, USD)"
```

---

## Task 8: Unpack `material_events` in `build_dossier` + enrich mock

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py`

- [ ] **Step 1: Write the failing test** — add to `tests/ingestion/test_dossier.py`:

```python
def test_build_dossier_unpacks_material_events():
    from datetime import date
    from saturn.models import MaterialEvent, Provenance, Quote

    def fake_edgar(ticker):
        return {
            "fundamentals": None,
            "filing_sections": [],
            "material_events": [
                MaterialEvent(filing_date=date(2024, 5, 22), item_codes=["2.02"], provenance=Provenance(source="SEC EDGAR"))
            ],
            "name": "NVIDIA CORP",
            "cik": "0001045810",
        }

    d = build_dossier(
        "NVDA", mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=fake_edgar, fred_fn=None,
    )
    assert len(d.material_events) == 1
    assert d.material_events[0].item_codes == ["2.02"]


def test_mock_dossier_has_quarterly_and_event():
    from saturn.ingestion.dossier import _mock_dossier
    d = _mock_dossier("NVDA")
    assert any(f.fiscal_period.startswith("Q") for f in d.fundamentals.facts)
    assert d.material_events and d.material_events[0].item_codes
```

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -k "material_events or quarterly_and_event" -v` → FAIL.

- [ ] **Step 2: Implement**

In `saturn/ingestion/dossier.py`:
- Add `MaterialEvent` to the `from saturn.models import (...)` block.
- In the edgar-result unpacking block, after `edgar_cik = edgar_result.get("cik")`, add:

```python
        material_events = edgar_result.get("material_events") or []
```
  and initialize `material_events = []` alongside `edgar_name = edgar_cik = None` (so it's defined when `edgar_result` is not a dict).
- In the `CompanyDossier(...)` construction, add `material_events=material_events,` after `filing_sections=filing_sections or [],`.
- In `_mock_dossier`, add a quarterly fact and a material event:
  - In the `Fundamentals(facts=[...])` list, append:
    ```python
                FinancialFact(concept="Revenues", value=30_040_000_000.0, unit="USD", fiscal_period="Q2 FY2025", provenance=prov_e),
    ```
  - Add `MaterialEvent` to the `from saturn.models import ...` at the top of dossier.py (already done above), and add a `material_events=[...]` argument to the `CompanyDossier(...)` in `_mock_dossier`:
    ```python
        material_events=[
            MaterialEvent(
                filing_date=date(2024, 5, 22),
                item_codes=["2.02", "9.01"],
                title="Results of Operations and Financial Condition",
                excerpt="[MOCK] Reported record quarterly revenue.",
                provenance=prov_e,
            )
        ],
    ```

- [ ] **Step 3: Run tests** — Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -v`. Expected: PASS (all dossier tests).

- [ ] **Step 4: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(ingestion): unpack material_events into the dossier; enrich mock with quarterly+event"
```

---

## Task 9: Report renderer — grouped financials + Material Events section

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_markdown_report.py`:

```python
def test_render_groups_financials_and_shows_events():
    md = render(_sample_report())  # uses _mock_dossier, now has a quarterly fact + event
    # quarterly row present
    assert "Q2 FY2025" in md
    # material events section present with the 8-K item label
    assert "## 14. Material Events (SEC 8-K)" in md
    assert "Results of Operations and Financial Condition" in md
    # sources/gaps renumbered after the new section
    assert "## 15. Sources" in md
```

Also UPDATE the existing `test_render_has_all_sections` expected-headers list: change `## 13. Macro Snapshot` stays; insert `## 14. Material Events (SEC 8-K)` and renumber `## 15. Sources` (was 14). (Data Gaps, when present, is now 16.)

Run: `.venv\Scripts\python.exe -m pytest tests/test_markdown_report.py -v` → FAIL.

- [ ] **Step 2: Implement**

In `saturn/reports/markdown_report.py`:

(a) Replace the **Section 5 (Financial Snapshot)** table-building loop so rows are grouped annual-first, then quarterly. Find the current block:

```python
        for fact in c.fundamentals.facts:
            val = _fmt_money(fact.value) if (fact.unit or "").upper() == "USD" else (
                fact.value if fact.value is not None else "N/A"
            )
            out.append(
                f"| {fact.concept} | {fact.fiscal_period or 'N/A'} | {val} "
                f"| {fact.unit or ''} | {fact.provenance.source} |"
            )
```

and replace the `for fact in c.fundamentals.facts:` line with a grouped iteration:

```python
        _annual = [x for x in c.fundamentals.facts if not (x.fiscal_period or "").startswith("Q")]
        _quarterly = [x for x in c.fundamentals.facts if (x.fiscal_period or "").startswith("Q")]
        for fact in _annual + _quarterly:
```

(keep the loop body that builds `val` and appends the row exactly as-is).

(b) After the **Macro Snapshot** section (## 13) and before the **Sources** section, insert a new Material Events section, and renumber Sources → 14→15 and Data Gaps → 15→16. Concretely, find:

```python
    out += ["## 14. Sources", ""]
```

and insert this block immediately before it, then change `14`→`15` on the Sources header and `15`→`16` on the Data Gaps header:

```python
    out += ["## 14. Material Events (SEC 8-K)", ""]
    if c.material_events:
        for ev in c.material_events:
            labels = ", ".join(
                f"{code}" for code in ev.item_codes
            )
            head = f"- **{ev.filing_date}**"
            if ev.title:
                head += f" — {ev.title}"
            if labels:
                head += f" (items {labels})"
            if ev.provenance.source_url:
                head += f" — [filing]({ev.provenance.source_url})"
            out.append(head)
            if ev.excerpt:
                out.append(f"  > {ev.excerpt}")
        out.append("")
    else:
        out.append("_No material events in the last 12 months._")
        out.append("")
```

Then update the subsequent headers:
- `out += ["## 14. Sources", ""]` → `out += ["## 15. Sources", ""]`
- `out += ["## 15. Data Gaps", ""]` → `out += ["## 16. Data Gaps", ""]`

- [ ] **Step 3: Run tests** — Run: `.venv\Scripts\python.exe -m pytest tests/test_markdown_report.py -v`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): group annual/quarterly financials; add Material Events (8-K) section"
```

---

## Task 10: Agent context — material events block

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_equity_research.py`:

```python
def test_company_context_includes_material_events():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.workflows.equity_research import _company_context

    ctx = _company_context(_mock_dossier("NVDA"))
    assert "MATERIAL EVENTS" in ctx
    assert "Results of Operations and Financial Condition" in ctx
    # quarterly fact is rendered too (provenance-tagged fundamentals loop)
    assert "Q2 FY2025" in ctx
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -k "material_events" -v` → FAIL.

- [ ] **Step 2: Implement**

In `saturn/workflows/equity_research.py`, in `_company_context`, after the `FILING SECTIONS` block and before the `MACRO` block, add:

```python
    if dossier.material_events:
        lines.append("\nMATERIAL EVENTS (SEC 8-K):")
        for ev in dossier.material_events:
            label = ev.title or ", ".join(ev.item_codes) or "8-K"
            lines.append(f"- {ev.filing_date}: {label} (source: {ev.provenance.source})")
            if ev.excerpt:
                lines.append(f"  {ev.excerpt}")
```

(The quarterly facts already render through the existing FUNDAMENTALS loop — no change needed there.)

- [ ] **Step 3: Run tests** — Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -v`. Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): render material events block in the agent context"
```

---

## Task 11: Full-suite + end-to-end verification

**Files:** none (verification; fix only genuine defects).

- [ ] **Step 1: Full offline suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass. If a Phase-0/earlier test broke from the renumber or the `EDGAR_CONCEPTS` restructure, fix it (update expected section numbers / concept references), then re-run.

- [ ] **Step 2: End-to-end mock smoke run**

Run: `.venv\Scripts\python.exe -m saturn.cli research NVDA --mock`
Expected: `[MOCK MODE] Wrote reports\NVDA_<DATE>.md`. Open the report and confirm: a quarterly row (`Q2 FY2025`) in the Financial Snapshot, a `## 14. Material Events (SEC 8-K)` section with the 2.02 entry + excerpt, `## 15. Sources`, and the disclaimer.

- [ ] **Step 3: Real-path offline-safe degradation check**

Run:
`.venv\Scripts\python.exe -c "import os; os.environ.pop('SEC_USER_AGENT', None); os.environ.pop('FRED_API_KEY', None); from saturn.ingestion.dossier import build_dossier; from saturn.models import Quote, Provenance; d = build_dossier('NVDA', mock=False, quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source='stub'))); print('GAPS:', sorted(g.source for g in d.gaps)); print('EVENTS:', d.material_events)"`
Expected: `GAPS: ['edgar', 'fred']`, `EVENTS: []`, no crash, no network.

- [ ] **Step 4: Commit any fix-ups**

```bash
git add -A
git commit -m "test: verify 10-Q/8-K/breadth expansion end-to-end"
```

---

## Self-Review

**Spec coverage (against `2026-06-06-edgar-10q-8k-expansion-design.md`):**
- §1 `MaterialEvent` + `material_events` → Task 1 ✓
- §2 file split (`edgar_filings.py`, generalized `_select_latest`) → Task 2 ✓
- §3 quarterly facts in `_parse_companyfacts` → Task 4 ✓; 10-Q MD&A + 8-K in `fetch_edgar` → Task 6 ✓
- §4 dossier unpack + report section + context block → Tasks 8, 9, 10 ✓
- §5 errors/caching (high-value-only excerpt, empty-window ≠ gap, cached full text) → Tasks 5, 6 ✓
- §6 testing (fixtures, offline) → every task; verified in Task 11 ✓
- §7.1 expanded concepts → Task 3 ✓; §7.2 multi-unit (EPS/shares) → Task 3 ✓; §7.3 expanded FRED → Task 7 ✓

**Placeholder scan:** No TBD/"handle edge cases"/"similar to". Every code step is complete. The one judgment note (Task 6 second test's manual `MonkeyPatch`) explicitly states the fallback. ✓

**Type consistency:** `MaterialEvent` fields (Task 1) used identically in `fetch_edgar` (Task 6), `build_dossier`/`_mock_dossier` (Task 8), renderer (Task 9), context (Task 10). `_select_latest(submissions, form)` defined Task 2, used Task 6. `_select_recent_8ks(submissions, *, since) -> [{form,accession,primary_document,filing_date,item_codes}]` defined Task 5, consumed Task 6. `_period_entries(tag_block, unit, *, annual)` defined Task 3, used (annual) Task 3 and (quarterly) Task 4. `EDGAR_CONCEPTS[c] = {"unit","tags"}` (Task 3) consumed in `_parse_companyfacts` (Tasks 3-4). `fetch_edgar` return dict gains `material_events` (Task 6), consumed in `build_dossier` (Task 8). Section numbers: Macro 13, Material Events 14, Sources 15, Data Gaps 16 (Task 9) — Task 11 checks them. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-edgar-10q-8k-expansion.md`. Recommended execution: subagent-driven (fresh subagent per task, two-stage review), same as the prior slices. Task 2 (the file-split refactor) is the highest-risk task — verify the full suite stays green before proceeding past it.

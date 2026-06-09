# Slice B — Derived Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, provenance-tagged analytics layer that computes professional trailing equity-research metrics from the as-reported `CompanyDossier`, each carrying its formula + exact input facts, with a code catalog as the single source of truth that also generates `docs/metrics.md`.

**Architecture:** A new pure `saturn/analytics/` package. `catalog.py` holds `METRIC_CATALOG` (metadata: name/category/format/formula/description/caveat) and renders the reference doc. `metrics.py` computes `DerivedMetric`s from `Fundamentals` + `Quote`, pulling each metric's format/formula from the catalog. `build_dossier` (and `_mock_dossier`) attach the result to `dossier.derived_metrics`. The LLM context and markdown report surface them; a `saturn metrics` CLI command and a drift-guard test keep `docs/metrics.md` in sync.

**Tech Stack:** Python 3.13, Pydantic v2, Typer, pytest. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-07-derived-metrics-design.md`

---

## File Structure

- **Create:**
  - `saturn/analytics/__init__.py` — package marker
  - `saturn/analytics/catalog.py` — `MetricDef`, `METRIC_CATALOG`, `render_metrics_reference()`, `METRICS_DOC_PATH`
  - `saturn/analytics/metrics.py` — `compute_metrics()` + family computations + helpers
  - `docs/metrics.md` — generated, committed reference
  - `tests/analytics/__init__.py`
  - `tests/analytics/test_catalog.py` — drift guard + name coverage
  - `tests/analytics/test_metrics.py` — per-family value/edge tests
- **Modify:**
  - `saturn/models.py` — `MetricInput`, `DerivedMetric`, `CompanyDossier.derived_metrics`
  - `saturn/ingestion/edgar.py` — two new `EDGAR_CONCEPTS` entries
  - `saturn/ingestion/dossier.py` — compute + attach (real + mock)
  - `saturn/workflows/equity_research.py` — DERIVED METRICS context block
  - `saturn/reports/markdown_report.py` — Key Metrics section + methodology link + renumber
  - `saturn/cli.py` — `metrics` command
  - `tests/test_equity_research.py`, `tests/test_markdown_report.py` — context/report assertions

**Canonical metric names** (catalog and `compute_metrics` must match exactly):
`gross_margin, operating_margin, net_margin, ebitda_margin, fcf_margin, roe, roa, roic, roce, current_ratio, quick_ratio, cash_ratio, debt_to_equity, debt_to_assets, net_debt, net_debt_to_ebitda, interest_coverage, asset_turnover, inventory_turnover, capex_intensity, days_sales_outstanding, fcf, fcf_conversion, revenue_growth_yoy, eps_growth_yoy, fcf_growth_yoy, revenue_cagr_3y, eps_cagr_3y, revenue_growth_qoq, fcf_per_share, book_value_per_share, effective_tax_rate, share_count_change_yoy, dividend_coverage, accruals_ratio, revenue_ttm, net_income_ttm, eps_ttm, pe_ratio, ps_ratio, pb_ratio, p_fcf, ev_ebitda, ev_sales, earnings_yield, dividend_yield, payout_ratio, buyback_yield, total_shareholder_yield`

---

## Task 1: Models — MetricInput, DerivedMetric, dossier field

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:
```python
def test_derived_metric_and_input_models():
    from saturn.models import DerivedMetric, MetricInput, Provenance, CompanyDossier
    from datetime import date

    m = DerivedMetric(
        name="gross_margin",
        value=0.744,
        format="percent",
        fiscal_period="Q2 FY2026",
        formula="GrossProfit / Revenues",
        inputs=[MetricInput(concept="GrossProfit", fiscal_period="Q2 FY2026", value=17_755e6, source="SEC EDGAR")],
        provenance=Provenance(source="Saturn (derived)", as_of=date(2026, 6, 8)),
    )
    assert m.value == 0.744 and m.format == "percent"
    assert m.inputs[0].concept == "GrossProfit"

    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 6, 8))
    assert d.derived_metrics == []          # default empty
    d.derived_metrics = [m]
    assert d.derived_metrics[0].name == "gross_margin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py::test_derived_metric_and_input_models -v`
Expected: FAIL with `ImportError: cannot import name 'DerivedMetric'`.

- [ ] **Step 3: Implement**

In `saturn/models.py`, after the `FinancialFact`/`Fundamentals` classes add:
```python
class MetricInput(BaseModel):
    """One source fact a derived metric consumed (for verification)."""

    concept: str
    fiscal_period: str | None = None
    value: float
    source: str


class DerivedMetric(BaseModel):
    """A deterministically computed metric carrying its formula and inputs."""

    name: str
    value: float
    format: str  # percent | ratio | currency | x | per_share
    fiscal_period: str | None = None
    formula: str
    inputs: list[MetricInput] = Field(default_factory=list)
    provenance: Provenance
```
And in `CompanyDossier`, add the field (next to `material_events`):
```python
    derived_metrics: list[DerivedMetric] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models.py
git commit -m "feat(models): DerivedMetric + MetricInput + dossier.derived_metrics"
```

---

## Task 2: Metric catalog (single source of truth) + generated docs/metrics.md

**Files:**
- Create: `saturn/analytics/__init__.py`, `saturn/analytics/catalog.py`, `docs/metrics.md`, `tests/analytics/__init__.py`, `tests/analytics/test_catalog.py`

- [ ] **Step 1: Write the failing test**

Create `tests/analytics/__init__.py` (empty) and `tests/analytics/test_catalog.py`:
```python
from saturn.analytics.catalog import (
    METRIC_CATALOG,
    METRICS_DOC_PATH,
    MetricDef,
    render_metrics_reference,
)

CANONICAL = {
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin", "fcf_margin",
    "roe", "roa", "roic", "roce",
    "current_ratio", "quick_ratio", "cash_ratio",
    "debt_to_equity", "debt_to_assets", "net_debt", "net_debt_to_ebitda", "interest_coverage",
    "asset_turnover", "inventory_turnover", "capex_intensity", "days_sales_outstanding",
    "fcf", "fcf_conversion",
    "revenue_growth_yoy", "eps_growth_yoy", "fcf_growth_yoy",
    "revenue_cagr_3y", "eps_cagr_3y", "revenue_growth_qoq",
    "fcf_per_share", "book_value_per_share",
    "effective_tax_rate", "share_count_change_yoy", "dividend_coverage", "accruals_ratio",
    "revenue_ttm", "net_income_ttm", "eps_ttm",
    "pe_ratio", "ps_ratio", "pb_ratio", "p_fcf", "ev_ebitda", "ev_sales",
    "earnings_yield", "dividend_yield", "payout_ratio",
    "buyback_yield", "total_shareholder_yield",
}


def test_catalog_covers_canonical_names_exactly():
    assert set(METRIC_CATALOG) == CANONICAL


def test_catalog_entries_well_formed():
    valid_fmt = {"percent", "ratio", "currency", "x", "per_share"}
    for name, d in METRIC_CATALOG.items():
        assert isinstance(d, MetricDef)
        assert d.name == name
        assert d.fmt in valid_fmt
        assert d.formula and d.description


def test_docs_metrics_md_is_in_sync():
    # drift guard: the committed doc must equal the generated output
    committed = METRICS_DOC_PATH.read_text(encoding="utf-8")
    assert committed == render_metrics_reference()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.analytics'`.

- [ ] **Step 3: Implement the catalog**

Create `saturn/analytics/__init__.py` (empty). Create `saturn/analytics/catalog.py`:
```python
"""Single source of truth for derived-metric metadata + reference-doc generator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

METRICS_DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "metrics.md"


@dataclass(frozen=True)
class MetricDef:
    name: str
    category: str
    fmt: str  # percent | ratio | currency | x | per_share
    formula: str
    description: str
    caveat: str | None = None


def _d(name, category, fmt, formula, description, caveat=None) -> MetricDef:
    return MetricDef(name, category, fmt, formula, description, caveat)


# Order here defines order in the generated doc.
_DEFS: list[MetricDef] = [
    # Profitability
    _d("gross_margin", "Profitability", "percent", "GrossProfit / Revenues", "Gross profit as a share of revenue."),
    _d("operating_margin", "Profitability", "percent", "OperatingIncomeLoss / Revenues", "Operating income as a share of revenue."),
    _d("net_margin", "Profitability", "percent", "NetIncomeLoss / Revenues", "Net income as a share of revenue."),
    _d("ebitda_margin", "Profitability", "percent", "(OperatingIncomeLoss + DepreciationAndAmortization) / Revenues", "EBITDA as a share of revenue.", "EBITDA approximated as operating income + D&A."),
    _d("fcf_margin", "Profitability", "percent", "(OperatingCashFlow - CapitalExpenditures) / Revenues", "Free cash flow as a share of revenue."),
    # Returns
    _d("roe", "Returns", "percent", "NetIncomeLoss / StockholdersEquity", "Return on equity."),
    _d("roa", "Returns", "percent", "NetIncomeLoss / Assets", "Return on assets."),
    _d("roic", "Returns", "percent", "(OperatingIncomeLoss * (1 - effective_tax_rate)) / (TotalDebt + StockholdersEquity)", "Return on invested capital.", "NOPAT approx = operating income x (1 - effective tax rate); invested capital approx = total debt + equity."),
    _d("roce", "Returns", "percent", "OperatingIncomeLoss / (Assets - LiabilitiesCurrent)", "Return on capital employed."),
    # Liquidity
    _d("current_ratio", "Liquidity", "ratio", "AssetsCurrent / LiabilitiesCurrent", "Short-term assets vs short-term liabilities."),
    _d("quick_ratio", "Liquidity", "ratio", "(AssetsCurrent - Inventory) / LiabilitiesCurrent", "Acid-test liquidity."),
    _d("cash_ratio", "Liquidity", "ratio", "CashAndCashEquivalents / LiabilitiesCurrent", "Cash vs short-term liabilities."),
    # Leverage
    _d("debt_to_equity", "Leverage", "ratio", "TotalDebt / StockholdersEquity", "Leverage relative to equity.", "TotalDebt = LongTermDebt + DebtCurrent (LongTermDebt alone if DebtCurrent absent)."),
    _d("debt_to_assets", "Leverage", "ratio", "TotalDebt / Assets", "Leverage relative to assets."),
    _d("net_debt", "Leverage", "currency", "TotalDebt - CashAndCashEquivalents", "Debt net of cash."),
    _d("net_debt_to_ebitda", "Leverage", "x", "(TotalDebt - CashAndCashEquivalents) / (OperatingIncomeLoss + DepreciationAndAmortization)", "Years of EBITDA to repay net debt."),
    _d("interest_coverage", "Leverage", "x", "OperatingIncomeLoss / InterestExpense", "Operating income vs interest expense."),
    # Efficiency
    _d("asset_turnover", "Efficiency", "x", "Revenues / Assets", "Revenue generated per dollar of assets."),
    _d("inventory_turnover", "Efficiency", "x", "CostOfRevenue / Inventory", "Cost of revenue vs inventory."),
    _d("capex_intensity", "Efficiency", "percent", "CapitalExpenditures / Revenues", "Capital spending as a share of revenue."),
    _d("days_sales_outstanding", "Efficiency", "ratio", "AccountsReceivableNetCurrent / Revenues * 365", "Average collection period (days), annual only."),
    # Cash
    _d("fcf", "Cash", "currency", "OperatingCashFlow - CapitalExpenditures", "Free cash flow."),
    _d("fcf_conversion", "Cash", "percent", "(OperatingCashFlow - CapitalExpenditures) / NetIncomeLoss", "How much net income converts to FCF (earnings quality)."),
    # Growth
    _d("revenue_growth_yoy", "Growth", "percent", "Revenues[t] / Revenues[t-1] - 1", "Year-over-year revenue growth."),
    _d("eps_growth_yoy", "Growth", "percent", "EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-1] - 1", "Year-over-year diluted EPS growth."),
    _d("fcf_growth_yoy", "Growth", "percent", "FCF[t] / FCF[t-1] - 1", "Year-over-year FCF growth."),
    _d("revenue_cagr_3y", "Growth", "percent", "(Revenues[t] / Revenues[t-3]) ** (1/3) - 1", "3-year revenue CAGR.", "Only when both endpoints are positive."),
    _d("eps_cagr_3y", "Growth", "percent", "(EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-3]) ** (1/3) - 1", "3-year diluted EPS CAGR.", "Only when both endpoints are positive."),
    _d("revenue_growth_qoq", "Growth", "percent", "Revenues[Q] / Revenues[Q-1] - 1", "Sequential quarter-over-quarter revenue growth."),
    # Per-share
    _d("fcf_per_share", "Per-share", "per_share", "(OperatingCashFlow - CapitalExpenditures) / WeightedAverageSharesDiluted", "Free cash flow per diluted share."),
    _d("book_value_per_share", "Per-share", "per_share", "StockholdersEquity / WeightedAverageSharesDiluted", "Book value per diluted share."),
    # Trailing-twelve-month
    _d("revenue_ttm", "Trailing-twelve-month", "currency", "sum(Revenues over last 4 single quarters)", "Trailing-twelve-month revenue."),
    _d("net_income_ttm", "Trailing-twelve-month", "currency", "sum(NetIncomeLoss over last 4 single quarters)", "Trailing-twelve-month net income."),
    _d("eps_ttm", "Trailing-twelve-month", "per_share", "sum(EarningsPerShareDiluted over last 4 single quarters)", "Trailing-twelve-month diluted EPS."),
    # Valuation
    _d("pe_ratio", "Valuation", "x", "market_cap / net_income_ttm", "Price/earnings (TTM, else latest FY)."),
    _d("ps_ratio", "Valuation", "x", "market_cap / revenue_ttm", "Price/sales (TTM, else latest FY)."),
    _d("pb_ratio", "Valuation", "x", "market_cap / StockholdersEquity", "Price/book."),
    _d("p_fcf", "Valuation", "x", "market_cap / FCF", "Price/free-cash-flow (latest FY)."),
    _d("ev_ebitda", "Valuation", "x", "(market_cap + net_debt) / EBITDA", "Enterprise value / EBITDA (latest FY).", "EV ignores leases, preferred, and minority interest."),
    _d("ev_sales", "Valuation", "x", "(market_cap + net_debt) / revenue_ttm", "Enterprise value / sales (TTM, else latest FY).", "EV ignores leases, preferred, and minority interest."),
    _d("earnings_yield", "Valuation", "percent", "net_income_ttm / market_cap", "Inverse of P/E."),
    _d("dividend_yield", "Valuation", "percent", "DividendsPaid / market_cap", "Trailing dividend yield (latest FY dividends)."),
    _d("payout_ratio", "Valuation", "percent", "DividendsPaid / NetIncomeLoss", "Dividends as a share of net income (latest FY)."),
    # Quality & capital return
    _d("effective_tax_rate", "Quality & capital return", "percent", "IncomeTaxExpenseBenefit / (NetIncomeLoss + IncomeTaxExpenseBenefit)", "Effective tax rate.", "Pretax income approximated as net income + tax expense."),
    _d("share_count_change_yoy", "Quality & capital return", "percent", "WeightedAverageSharesDiluted[t] / WeightedAverageSharesDiluted[t-1] - 1", "Diluted share-count change (dilution signal)."),
    _d("dividend_coverage", "Quality & capital return", "x", "(OperatingCashFlow - CapitalExpenditures) / DividendsPaid", "FCF coverage of dividends (latest FY)."),
    _d("accruals_ratio", "Quality & capital return", "percent", "(NetIncomeLoss - OperatingCashFlow) / Assets", "Accruals vs assets; high values flag lower earnings quality."),
    _d("buyback_yield", "Quality & capital return", "percent", "StockRepurchased / market_cap", "Buyback yield (latest FY repurchases)."),
    _d("total_shareholder_yield", "Quality & capital return", "percent", "(DividendsPaid + StockRepurchased) / market_cap", "Dividends + buybacks vs market cap (latest FY)."),
]

METRIC_CATALOG: dict[str, MetricDef] = {d.name: d for d in _DEFS}

_CATEGORY_ORDER = [
    "Profitability", "Returns", "Liquidity", "Leverage", "Efficiency", "Cash",
    "Growth", "Per-share", "Trailing-twelve-month", "Valuation",
    "Quality & capital return",
]


def render_metrics_reference() -> str:
    """Render METRIC_CATALOG to the canonical docs/metrics.md markdown."""
    lines = [
        "# Saturn Metric Definitions",
        "",
        "_Generated from `saturn/analytics/catalog.py` — do not edit by hand; "
        "run `saturn metrics --write` to regenerate._",
        "",
    ]
    by_cat: dict[str, list[MetricDef]] = {}
    for d in METRIC_CATALOG.values():
        by_cat.setdefault(d.category, []).append(d)
    for cat in _CATEGORY_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        lines += [f"## {cat}", "", "| Metric | Format | Formula | Notes |", "| --- | --- | --- | --- |"]
        for d in items:
            lines.append(f"| `{d.name}` | {d.fmt} | {d.formula} | {d.caveat or ''} |")
        lines.append("")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Generate the committed doc, then run the tests**

Generate the file (one-off; the `saturn metrics --write` command in Task 15 wraps this):
```bash
.venv/Scripts/python.exe -c "from saturn.analytics.catalog import render_metrics_reference, METRICS_DOC_PATH; METRICS_DOC_PATH.write_text(render_metrics_reference(), encoding='utf-8'); print('wrote', METRICS_DOC_PATH)"
```
Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_catalog.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/__init__.py saturn/analytics/catalog.py docs/metrics.md tests/analytics/__init__.py tests/analytics/test_catalog.py
git commit -m "feat(analytics): metric catalog source-of-truth + generated docs/metrics.md"
```

---

## Task 3: EDGAR — add DebtCurrent + AccountsReceivableNetCurrent concepts

**Files:**
- Modify: `saturn/ingestion/edgar.py:48-64` (the `EDGAR_CONCEPTS` dict)
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_edgar.py`:
```python
def test_parse_includes_debt_current_and_receivables():
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {
            "DebtCurrent": {"units": {"USD": [
                {"end": "2025-12-31", "val": 1_000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-15"},
            ]}},
            "AccountsReceivableNetCurrent": {"units": {"USD": [
                {"end": "2025-12-31", "val": 2_000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-15"},
            ]}},
        }},
    }
    f = _parse_companyfacts(payload)
    concepts = {x.concept for x in f.facts}
    assert "DebtCurrent" in concepts
    assert "AccountsReceivableNetCurrent" in concepts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py::test_parse_includes_debt_current_and_receivables -v`
Expected: FAIL (concepts not found — they aren't in `EDGAR_CONCEPTS`).

- [ ] **Step 3: Implement**

In `saturn/ingestion/edgar.py`, inside `EDGAR_CONCEPTS`, in the balance-sheet block add:
```python
    "DebtCurrent": {"unit": "USD", "tags": ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"]},
    "AccountsReceivableNetCurrent": {"unit": "USD", "tags": ["AccountsReceivableNetCurrent"]},
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py -v`
Expected: PASS (new test + all existing EDGAR tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py
git commit -m "feat(edgar): ingest DebtCurrent + AccountsReceivableNetCurrent"
```

---

## Task 4: metrics.py scaffolding + profitability margins

**Files:**
- Create: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

This task builds the shared helpers and the first metric family end-to-end (catalog → compute → `DerivedMetric` whose format/formula come from the catalog).

- [ ] **Step 1: Write the failing test**

Create `tests/analytics/test_metrics.py`:
```python
from datetime import date

from saturn.analytics.metrics import compute_metrics
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _facts(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=PROV)
        for (c, p, v) in rows
    ])


def _by_name(metrics, name, period):
    return next((m for m in metrics if m.name == name and m.fiscal_period == period), None)


def test_profitability_margins_and_provenance():
    f = _facts([
        ("Revenues", "FY2025", 1000.0),
        ("GrossProfit", "FY2025", 600.0),
        ("OperatingIncomeLoss", "FY2025", 250.0),
        ("NetIncomeLoss", "FY2025", 200.0),
    ])
    ms = compute_metrics(f, None)
    gm = _by_name(ms, "gross_margin", "FY2025")
    assert gm is not None and abs(gm.value - 0.6) < 1e-9
    assert gm.format == "percent"                     # pulled from catalog
    assert gm.formula == "GrossProfit / Revenues"     # pulled from catalog
    assert gm.provenance.source == "Saturn (derived)"
    assert {i.concept for i in gm.inputs} == {"GrossProfit", "Revenues"}
    assert abs(_by_name(ms, "operating_margin", "FY2025").value - 0.25) < 1e-9
    assert abs(_by_name(ms, "net_margin", "FY2025").value - 0.20) < 1e-9


def test_zero_denominator_and_missing_input_skip():
    f = _facts([("GrossProfit", "FY2025", 600.0), ("Revenues", "FY2025", 0.0)])
    ms = compute_metrics(f, None)
    assert _by_name(ms, "gross_margin", "FY2025") is None   # zero revenue -> skipped
    f2 = _facts([("Revenues", "FY2025", 1000.0)])           # no GrossProfit
    assert _by_name(compute_metrics(f2, None), "gross_margin", "FY2025") is None


def test_negative_value_passes_through():
    f = _facts([("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", -300.0)])
    nm = _by_name(compute_metrics(f, None), "net_margin", "FY2025")
    assert nm is not None and abs(nm.value - (-0.3)) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.analytics.metrics'`.

- [ ] **Step 3: Implement scaffolding + profitability**

Create `saturn/analytics/metrics.py`:
```python
"""Deterministic derived-metric computation over the as-reported dossier.

Pure and offline. Each metric's format/formula come from METRIC_CATALOG so the
number, the report, and docs/metrics.md never disagree.
"""

from __future__ import annotations

from datetime import date

from saturn.analytics.catalog import METRIC_CATALOG
from saturn.models import (
    DerivedMetric,
    FinancialFact,
    Fundamentals,
    MetricInput,
    Provenance,
    Quote,
)

# ----- shared helpers --------------------------------------------------------


def _index(fundamentals: Fundamentals | None) -> dict[tuple[str, str], FinancialFact]:
    out: dict[tuple[str, str], FinancialFact] = {}
    if fundamentals:
        for f in fundamentals.facts:
            if f.fiscal_period is not None and f.value is not None:
                out[(f.concept, f.fiscal_period)] = f
    return out


def _fact(idx, concept: str, period: str) -> FinancialFact | None:
    return idx.get((concept, period))


def _in(fact: FinancialFact) -> MetricInput:
    return MetricInput(
        concept=fact.concept,
        fiscal_period=fact.fiscal_period,
        value=fact.value,
        source=fact.provenance.source,
    )


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _make(name: str, value: float | None, period: str | None, inputs: list[MetricInput]) -> DerivedMetric | None:
    if value is None:
        return None
    d = METRIC_CATALOG[name]
    return DerivedMetric(
        name=name,
        value=value,
        format=d.fmt,
        fiscal_period=period,
        formula=d.formula,
        inputs=inputs,
        provenance=Provenance(source="Saturn (derived)", as_of=date.today()),
    )


def _ratio(idx, period, name, num_concept, den_concept) -> DerivedMetric | None:
    a = _fact(idx, num_concept, period)
    b = _fact(idx, den_concept, period)
    if not a or not b:
        return None
    return _make(name, _div(a.value, b.value), period, [_in(a), _in(b)])


def _annual_periods(idx) -> list[str]:
    ps = {p for (_c, p) in idx if p.startswith("FY")}
    return sorted(ps, key=lambda p: int(p[2:]), reverse=True)


def _quarterly_periods(idx) -> list[str]:
    ps = {p for (_c, p) in idx if p.startswith("Q")}

    def key(p: str) -> tuple[int, int]:
        q, fy = p.split()
        return (int(fy[2:]), int(q[1]))

    return sorted(ps, key=key, reverse=True)


def _fcf(idx, period) -> tuple[float, list[MetricInput]] | None:
    ocf = _fact(idx, "OperatingCashFlow", period)
    capex = _fact(idx, "CapitalExpenditures", period)
    if not ocf or not capex:
        return None
    return (ocf.value - capex.value, [_in(ocf), _in(capex)])


# ----- metric families -------------------------------------------------------


def _profitability(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "gross_margin", "GrossProfit", "Revenues"),
        _ratio(idx, period, "operating_margin", "OperatingIncomeLoss", "Revenues"),
        _ratio(idx, period, "net_margin", "NetIncomeLoss", "Revenues"),
    ]
    rev = _fact(idx, "Revenues", period)
    oi = _fact(idx, "OperatingIncomeLoss", period)
    da = _fact(idx, "DepreciationAndAmortization", period)
    if rev and oi and da:
        out.append(_make("ebitda_margin", _div(oi.value + da.value, rev.value), period, [_in(oi), _in(da), _in(rev)]))
    fcf = _fcf(idx, period)
    if rev and fcf:
        out.append(_make("fcf_margin", _div(fcf[0], rev.value), period, fcf[1] + [_in(rev)]))
    return out


# ----- entry point -----------------------------------------------------------


def compute_metrics(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
    idx = _index(fundamentals)
    out: list[DerivedMetric | None] = []
    for period in _annual_periods(idx) + _quarterly_periods(idx):
        out += _profitability(idx, period)
    return [m for m in out if m]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): compute scaffolding + profitability margins"
```

---

## Task 5: Returns + effective tax rate

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def test_returns_and_effective_tax_rate():
    f = _facts([
        ("NetIncomeLoss", "FY2025", 200.0),
        ("StockholdersEquity", "FY2025", 1000.0),
        ("Assets", "FY2025", 2500.0),
        ("LiabilitiesCurrent", "FY2025", 500.0),
        ("OperatingIncomeLoss", "FY2025", 300.0),
        ("IncomeTaxExpenseBenefit", "FY2025", 50.0),
        ("LongTermDebt", "FY2025", 400.0),
        ("DebtCurrent", "FY2025", 100.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "roe", "FY2025").value - 0.20) < 1e-9
    assert abs(_by_name(ms, "roa", "FY2025").value - 0.08) < 1e-9
    # effective tax rate = 50 / (200 + 50) = 0.20
    etr = _by_name(ms, "effective_tax_rate", "FY2025")
    assert abs(etr.value - 0.20) < 1e-9
    # roce = 300 / (2500 - 500) = 0.15
    assert abs(_by_name(ms, "roce", "FY2025").value - 0.15) < 1e-9
    # roic = (300 * (1 - 0.20)) / (500 + 1000) = 240 / 1500 = 0.16
    assert abs(_by_name(ms, "roic", "FY2025").value - 0.16) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_returns_and_effective_tax_rate -v`
Expected: FAIL (metrics return None / not found).

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py` a helper and family, and call it in `compute_metrics`:
```python
def _effective_tax_rate_value(idx, period) -> tuple[float, list[MetricInput]] | None:
    ni = _fact(idx, "NetIncomeLoss", period)
    tax = _fact(idx, "IncomeTaxExpenseBenefit", period)
    if not ni or not tax:
        return None
    pretax = ni.value + tax.value
    v = _div(tax.value, pretax)
    if v is None:
        return None
    return (v, [_in(tax), _in(ni)])


def _returns(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "roe", "NetIncomeLoss", "StockholdersEquity"),
        _ratio(idx, period, "roa", "NetIncomeLoss", "Assets"),
    ]
    assets = _fact(idx, "Assets", period)
    lc = _fact(idx, "LiabilitiesCurrent", period)
    oi = _fact(idx, "OperatingIncomeLoss", period)
    if oi and assets and lc:
        out.append(_make("roce", _div(oi.value, assets.value - lc.value), period, [_in(oi), _in(assets), _in(lc)]))
    etr = _effective_tax_rate_value(idx, period)
    if etr:
        out.append(_make("effective_tax_rate", etr[0], period, etr[1]))
    eq = _fact(idx, "StockholdersEquity", period)
    ltd = _fact(idx, "LongTermDebt", period)
    if oi and etr and eq and ltd:
        dc = _fact(idx, "DebtCurrent", period)
        total_debt = ltd.value + (dc.value if dc else 0.0)
        nopat = oi.value * (1 - etr[0])
        inputs = [_in(oi), _in(eq), _in(ltd)] + ([_in(dc)] if dc else [])
        out.append(_make("roic", _div(nopat, total_debt + eq.value), period, inputs))
    return out
```
In `compute_metrics`, add `out += _returns(idx, period)` inside the period loop (after `_profitability`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): returns (roe/roa/roce/roic) + effective tax rate"
```

---

## Task 6: Liquidity + leverage

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def test_liquidity_and_leverage():
    f = _facts([
        ("AssetsCurrent", "FY2025", 2000.0),
        ("LiabilitiesCurrent", "FY2025", 1000.0),
        ("Inventory", "FY2025", 400.0),
        ("CashAndCashEquivalents", "FY2025", 300.0),
        ("LongTermDebt", "FY2025", 800.0),
        ("DebtCurrent", "FY2025", 200.0),
        ("StockholdersEquity", "FY2025", 2000.0),
        ("Assets", "FY2025", 5000.0),
        ("OperatingIncomeLoss", "FY2025", 500.0),
        ("DepreciationAndAmortization", "FY2025", 100.0),
        ("InterestExpense", "FY2025", 50.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "current_ratio", "FY2025").value - 2.0) < 1e-9
    assert abs(_by_name(ms, "quick_ratio", "FY2025").value - 1.6) < 1e-9      # (2000-400)/1000
    assert abs(_by_name(ms, "cash_ratio", "FY2025").value - 0.3) < 1e-9
    assert abs(_by_name(ms, "debt_to_equity", "FY2025").value - 0.5) < 1e-9   # 1000/2000
    assert abs(_by_name(ms, "debt_to_assets", "FY2025").value - 0.2) < 1e-9   # 1000/5000
    assert abs(_by_name(ms, "net_debt", "FY2025").value - 700.0) < 1e-9       # 1000-300
    # net_debt_to_ebitda = 700 / (500+100) = 1.1667
    assert abs(_by_name(ms, "net_debt_to_ebitda", "FY2025").value - (700.0 / 600.0)) < 1e-9
    assert abs(_by_name(ms, "interest_coverage", "FY2025").value - 10.0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_liquidity_and_leverage -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py`:
```python
def _total_debt(idx, period) -> tuple[float, list[MetricInput]] | None:
    ltd = _fact(idx, "LongTermDebt", period)
    if not ltd:
        return None
    dc = _fact(idx, "DebtCurrent", period)
    total = ltd.value + (dc.value if dc else 0.0)
    return (total, [_in(ltd)] + ([_in(dc)] if dc else []))


def _ebitda(idx, period) -> tuple[float, list[MetricInput]] | None:
    oi = _fact(idx, "OperatingIncomeLoss", period)
    da = _fact(idx, "DepreciationAndAmortization", period)
    if not oi or not da:
        return None
    return (oi.value + da.value, [_in(oi), _in(da)])


def _liquidity(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "current_ratio", "AssetsCurrent", "LiabilitiesCurrent"),
        _ratio(idx, period, "cash_ratio", "CashAndCashEquivalents", "LiabilitiesCurrent"),
    ]
    ac = _fact(idx, "AssetsCurrent", period)
    inv = _fact(idx, "Inventory", period)
    lc = _fact(idx, "LiabilitiesCurrent", period)
    if ac and inv and lc:
        out.append(_make("quick_ratio", _div(ac.value - inv.value, lc.value), period, [_in(ac), _in(inv), _in(lc)]))
    return out


def _leverage(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    td = _total_debt(idx, period)
    eq = _fact(idx, "StockholdersEquity", period)
    assets = _fact(idx, "Assets", period)
    cash = _fact(idx, "CashAndCashEquivalents", period)
    if td and eq:
        out.append(_make("debt_to_equity", _div(td[0], eq.value), period, td[1] + [_in(eq)]))
    if td and assets:
        out.append(_make("debt_to_assets", _div(td[0], assets.value), period, td[1] + [_in(assets)]))
    if td and cash:
        out.append(_make("net_debt", td[0] - cash.value, period, td[1] + [_in(cash)]))
        ebitda = _ebitda(idx, period)
        if ebitda:
            out.append(_make("net_debt_to_ebitda", _div(td[0] - cash.value, ebitda[0]), period, td[1] + [_in(cash)] + ebitda[1]))
    out.append(_ratio(idx, period, "interest_coverage", "OperatingIncomeLoss", "InterestExpense"))
    return out
```
In `compute_metrics`, add inside the loop: `out += _liquidity(idx, period)` and `out += _leverage(idx, period)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): liquidity + leverage metrics"
```

---

## Task 7: Efficiency + cash (fcf, fcf_conversion)

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def test_efficiency_and_cash():
    f = _facts([
        ("Revenues", "FY2025", 1000.0),
        ("Assets", "FY2025", 2000.0),
        ("CostOfRevenue", "FY2025", 600.0),
        ("Inventory", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("AccountsReceivableNetCurrent", "FY2025", 200.0),
        ("OperatingCashFlow", "FY2025", 350.0),
        ("NetIncomeLoss", "FY2025", 250.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "asset_turnover", "FY2025").value - 0.5) < 1e-9
    assert abs(_by_name(ms, "inventory_turnover", "FY2025").value - 2.0) < 1e-9
    assert abs(_by_name(ms, "capex_intensity", "FY2025").value - 0.1) < 1e-9
    # dso = 200 / 1000 * 365 = 73
    assert abs(_by_name(ms, "days_sales_outstanding", "FY2025").value - 73.0) < 1e-9
    # fcf = 350 - 100 = 250
    assert abs(_by_name(ms, "fcf", "FY2025").value - 250.0) < 1e-9
    # fcf_conversion = 250 / 250 = 1.0
    assert abs(_by_name(ms, "fcf_conversion", "FY2025").value - 1.0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_efficiency_and_cash -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py`:
```python
def _efficiency(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "asset_turnover", "Revenues", "Assets"),
        _ratio(idx, period, "inventory_turnover", "CostOfRevenue", "Inventory"),
        _ratio(idx, period, "capex_intensity", "CapitalExpenditures", "Revenues"),
    ]
    # DSO is an annual figure (x365); skip for quarterly periods.
    if period.startswith("FY"):
        ar = _fact(idx, "AccountsReceivableNetCurrent", period)
        rev = _fact(idx, "Revenues", period)
        if ar and rev and rev.value != 0:
            out.append(_make("days_sales_outstanding", ar.value / rev.value * 365, period, [_in(ar), _in(rev)]))
    return out


def _cash(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    fcf = _fcf(idx, period)
    if fcf:
        out.append(_make("fcf", fcf[0], period, fcf[1]))
        ni = _fact(idx, "NetIncomeLoss", period)
        if ni:
            out.append(_make("fcf_conversion", _div(fcf[0], ni.value), period, fcf[1] + [_in(ni)]))
    return out
```
Note on DSO: `_div(ar.value, rev.value) and (...)` yields `None` when revenue is 0 (so the metric is skipped), else the days value.

In `compute_metrics`, add inside the loop: `out += _efficiency(idx, period)` and `out += _cash(idx, period)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): efficiency + cash (fcf, fcf_conversion)"
```

---

## Task 8: Growth (YoY, CAGR, QoQ)

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def test_growth_yoy_cagr_qoq():
    f = _facts([
        ("Revenues", "FY2025", 1200.0),
        ("Revenues", "FY2024", 1000.0),
        ("Revenues", "FY2022", 600.0),
        ("EarningsPerShareDiluted", "FY2025", 5.0),
        ("EarningsPerShareDiluted", "FY2024", 4.0),
        ("OperatingCashFlow", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("OperatingCashFlow", "FY2024", 250.0),
        ("CapitalExpenditures", "FY2024", 100.0),
        ("Revenues", "Q2 FY2025", 320.0),
        ("Revenues", "Q1 FY2025", 300.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "revenue_growth_yoy", "FY2025").value - 0.2) < 1e-9
    assert abs(_by_name(ms, "eps_growth_yoy", "FY2025").value - 0.25) < 1e-9
    # fcf FY2025 = 200, FY2024 = 150 -> 0.3333
    assert abs(_by_name(ms, "fcf_growth_yoy", "FY2025").value - (200.0 / 150.0 - 1)) < 1e-9
    # revenue_cagr_3y at FY2025 over FY2022: (1200/600)^(1/3)-1
    assert abs(_by_name(ms, "revenue_cagr_3y", "FY2025").value - ((1200.0 / 600.0) ** (1 / 3) - 1)) < 1e-9
    # qoq at Q2 FY2025: 320/300 - 1
    assert abs(_by_name(ms, "revenue_growth_qoq", "Q2 FY2025").value - (320.0 / 300.0 - 1)) < 1e-9


def test_cagr_skips_nonpositive_base():
    f = _facts([("EarningsPerShareDiluted", "FY2025", 5.0), ("EarningsPerShareDiluted", "FY2022", -1.0)])
    assert _by_name(compute_metrics(f, None), "eps_cagr_3y", "FY2025") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_growth_yoy_cagr_qoq -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py`:
```python
def _gr(a: float | None, b: float | None) -> float | None:
    """Growth ratio a/b - 1, or None when b is missing/zero. Handles a == 0."""
    if a is None or b is None or b == 0:
        return None
    return a / b - 1


def _prev_fy(period: str, back: int = 1) -> str:
    return f"FY{int(period[2:]) - back}"


def _prev_quarter(period: str) -> str:
    q, fy = period.split()
    n, y = int(q[1]), int(fy[2:])
    return f"Q4 FY{y - 1}" if n == 1 else f"Q{n - 1} FY{y}"


def _yoy(idx, period, name, concept) -> DerivedMetric | None:
    if period.startswith("FY"):
        prev = _prev_fy(period)
    else:
        q, fy = period.split()
        prev = f"{q} FY{int(fy[2:]) - 1}"   # same quarter, prior year
    a = _fact(idx, concept, period)
    b = _fact(idx, concept, prev)
    if not a or not b:
        return None
    return _make(name, _gr(a.value, b.value), period, [_in(a), _in(b)])


def _cagr(idx, period, name, concept, years=3) -> DerivedMetric | None:
    if not period.startswith("FY"):
        return None
    a = _fact(idx, concept, period)
    b = _fact(idx, concept, _prev_fy(period, years))
    if not a or not b or a.value <= 0 or b.value <= 0:
        return None
    return _make(name, (a.value / b.value) ** (1 / years) - 1, period, [_in(a), _in(b)])


def _growth(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = [
        _yoy(idx, period, "revenue_growth_yoy", "Revenues"),
        _yoy(idx, period, "eps_growth_yoy", "EarningsPerShareDiluted"),
        _cagr(idx, period, "revenue_cagr_3y", "Revenues"),
        _cagr(idx, period, "eps_cagr_3y", "EarningsPerShareDiluted"),
    ]
    # fcf_growth_yoy (annual): needs fcf at period and prior FY
    if period.startswith("FY"):
        cur = _fcf(idx, period)
        prev = _fcf(idx, _prev_fy(period))
        if cur and prev and prev[0] != 0:
            out.append(_make("fcf_growth_yoy", cur[0] / prev[0] - 1, period, cur[1] + prev[1]))
    # qoq (quarterly only)
    if period.startswith("Q"):
        a = _fact(idx, "Revenues", period)
        b = _fact(idx, "Revenues", _prev_quarter(period))
        if a and b:
            out.append(_make("revenue_growth_qoq", _gr(a.value, b.value), period, [_in(a), _in(b)]))
    return out
```
In `compute_metrics`, add inside the loop: `out += _growth(idx, period)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): growth (yoy, cagr, qoq)"
```

---

## Task 9: Per-share + quality/capital-return

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def test_per_share_and_quality():
    f = _facts([
        ("OperatingCashFlow", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2024", 80.0),
        ("StockholdersEquity", "FY2025", 1000.0),
        ("NetIncomeLoss", "FY2025", 250.0),
        ("IncomeTaxExpenseBenefit", "FY2025", 50.0),
        ("Assets", "FY2025", 2500.0),
        ("DividendsPaid", "FY2025", 50.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "fcf_per_share", "FY2025").value - 2.0) < 1e-9      # 200/100
    assert abs(_by_name(ms, "book_value_per_share", "FY2025").value - 10.0) < 1e-9
    # share_count_change_yoy = 100/80 - 1 = 0.25
    assert abs(_by_name(ms, "share_count_change_yoy", "FY2025").value - 0.25) < 1e-9
    # dividend_coverage = fcf 200 / dividends 50 = 4.0
    assert abs(_by_name(ms, "dividend_coverage", "FY2025").value - 4.0) < 1e-9
    # accruals_ratio = (250 - 300) / 2500 = -0.02
    assert abs(_by_name(ms, "accruals_ratio", "FY2025").value - (-0.02)) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_per_share_and_quality -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py`:
```python
def _per_share(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    sh = _fact(idx, "WeightedAverageSharesDiluted", period)
    if not sh:
        return out
    fcf = _fcf(idx, period)
    if fcf:
        out.append(_make("fcf_per_share", _div(fcf[0], sh.value), period, fcf[1] + [_in(sh)]))
    eq = _fact(idx, "StockholdersEquity", period)
    if eq:
        out.append(_make("book_value_per_share", _div(eq.value, sh.value), period, [_in(eq), _in(sh)]))
    return out


def _quality(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    etr = _effective_tax_rate_value(idx, period)
    if etr:
        out.append(_make("effective_tax_rate", etr[0], period, etr[1]))
    if period.startswith("FY"):
        a = _fact(idx, "WeightedAverageSharesDiluted", period)
        b = _fact(idx, "WeightedAverageSharesDiluted", _prev_fy(period))
        if a and b:
            out.append(_make("share_count_change_yoy", _gr(a.value, b.value), period, [_in(a), _in(b)]))
    fcf = _fcf(idx, period)
    div = _fact(idx, "DividendsPaid", period)
    if fcf and div:
        out.append(_make("dividend_coverage", _div(fcf[0], div.value), period, fcf[1] + [_in(div)]))
    ni = _fact(idx, "NetIncomeLoss", period)
    ocf = _fact(idx, "OperatingCashFlow", period)
    assets = _fact(idx, "Assets", period)
    if ni and ocf and assets:
        out.append(_make("accruals_ratio", _div(ni.value - ocf.value, assets.value), period, [_in(ni), _in(ocf), _in(assets)]))
    return out
```
Note: `effective_tax_rate` is now emitted by `_quality`; remove its emission from `_returns` to avoid a duplicate. In `_returns`, **keep** the line `etr = _effective_tax_rate_value(idx, period)` (roic still needs `etr`), but **delete** the two lines that emit it:
```python
    if etr:
        out.append(_make("effective_tax_rate", etr[0], period, etr[1]))
```

In `compute_metrics`, add inside the loop: `out += _per_share(idx, period)` and `out += _quality(idx, period)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS. Confirm no duplicate `effective_tax_rate` for a period:
```python
def test_effective_tax_rate_not_duplicated():
    f = _facts([("NetIncomeLoss", "FY2025", 200.0), ("IncomeTaxExpenseBenefit", "FY2025", 50.0)])
    etrs = [m for m in compute_metrics(f, None) if m.name == "effective_tax_rate" and m.fiscal_period == "FY2025"]
    assert len(etrs) == 1
```

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): per-share + quality/capital-return metrics"
```

---

## Task 10: TTM aggregates + valuation multiples

**Files:**
- Modify: `saturn/analytics/metrics.py`, `tests/analytics/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_metrics.py`:
```python
def _quote(market_cap=10_000.0):
    return Quote(price=100.0, market_cap=market_cap, currency="USD", provenance=Provenance(source="yfinance"))


def test_ttm_and_valuation():
    rows = []
    # 4 single quarters of revenue/net income/eps -> TTM
    for i, q in enumerate(["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"]):
        rows += [("Revenues", q, 250.0), ("NetIncomeLoss", q, 50.0), ("EarningsPerShareDiluted", q, 1.0)]
    rows += [
        ("StockholdersEquity", "FY2025", 5000.0),
        ("OperatingCashFlow", "FY2025", 1200.0),
        ("CapitalExpenditures", "FY2025", 200.0),
        ("DepreciationAndAmortization", "FY2025", 100.0),
        ("OperatingIncomeLoss", "FY2025", 900.0),
        ("LongTermDebt", "FY2025", 1000.0),
        ("CashAndCashEquivalents", "FY2025", 400.0),
        ("DividendsPaid", "FY2025", 100.0),
        ("StockRepurchased", "FY2025", 300.0),
        ("Revenues", "FY2025", 1000.0),
        ("NetIncomeLoss", "FY2025", 200.0),
    ]
    ms = compute_metrics(_facts(rows), _quote(market_cap=10_000.0))
    # TTM: revenue 1000, net income 200, eps 4
    assert abs(_by_name(ms, "revenue_ttm", "TTM").value - 1000.0) < 1e-9
    assert abs(_by_name(ms, "net_income_ttm", "TTM").value - 200.0) < 1e-9
    assert abs(_by_name(ms, "eps_ttm", "TTM").value - 4.0) < 1e-9
    # pe = 10000 / 200 = 50 ; ps = 10000/1000 = 10 ; pb = 10000/5000 = 2
    assert abs(_by_name(ms, "pe_ratio", "TTM").value - 50.0) < 1e-9
    assert abs(_by_name(ms, "ps_ratio", "TTM").value - 10.0) < 1e-9
    assert abs(_by_name(ms, "pb_ratio", "FY2025").value - 2.0) < 1e-9
    # net_debt = 600 ; EV = 10600 ; EBITDA = 1000 ; ev_ebitda = 10.6
    assert abs(_by_name(ms, "ev_ebitda", "FY2025").value - 10.6) < 1e-9
    # dividend_yield = 100/10000 = 0.01 ; buyback_yield = 300/10000 = 0.03 ; total = 0.04
    assert abs(_by_name(ms, "dividend_yield", "FY2025").value - 0.01) < 1e-9
    assert abs(_by_name(ms, "total_shareholder_yield", "FY2025").value - 0.04) < 1e-9


def test_valuation_skipped_without_quote():
    f = _facts([("NetIncomeLoss", "FY2025", 200.0)])
    assert _by_name(compute_metrics(f, None), "pe_ratio", "TTM") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_ttm_and_valuation -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `saturn/analytics/metrics.py`:
```python
def _ttm(idx, concept) -> tuple[float, list[MetricInput]] | None:
    qs = _quarterly_periods(idx)[:4]
    facts = [_fact(idx, concept, q) for q in qs]
    facts = [f for f in facts if f]
    if len(facts) < 4:
        return None
    return (sum(f.value for f in facts), [_in(f) for f in facts])


def _ttm_or_fy(idx, concept) -> tuple[float, str, list[MetricInput]] | None:
    t = _ttm(idx, concept)
    if t:
        return (t[0], "TTM", t[1])
    fy = _annual_periods(idx)
    if fy:
        f = _fact(idx, concept, fy[0])
        if f:
            return (f.value, fy[0], [_in(f)])
    return None


def _mcap_input(quote: Quote) -> MetricInput:
    return MetricInput(concept="market_cap", fiscal_period=None, value=quote.market_cap, source=quote.provenance.source)


def _ttm_metrics(idx) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    for name, concept in (("revenue_ttm", "Revenues"), ("net_income_ttm", "NetIncomeLoss"), ("eps_ttm", "EarningsPerShareDiluted")):
        t = _ttm(idx, concept)
        if t:
            out.append(_make(name, t[0], "TTM", t[1]))
    return out


def _valuation(idx, quote: Quote | None) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = list(_ttm_metrics(idx))
    if quote is None or quote.market_cap is None:
        return out
    mc = quote.market_cap
    mci = _mcap_input(quote)
    fy = _annual_periods(idx)
    latest_fy = fy[0] if fy else None

    # price multiples driven by TTM (else latest FY)
    ni = _ttm_or_fy(idx, "NetIncomeLoss")
    if ni:
        out.append(_make("pe_ratio", _div(mc, ni[0]), ni[1], [mci] + ni[2]))
        out.append(_make("earnings_yield", _div(ni[0], mc), ni[1], ni[2] + [mci]))
    rev = _ttm_or_fy(idx, "Revenues")
    if rev:
        out.append(_make("ps_ratio", _div(mc, rev[0]), rev[1], [mci] + rev[2]))
    eq = _fact(idx, "StockholdersEquity", latest_fy) if latest_fy else None
    if eq:
        out.append(_make("pb_ratio", _div(mc, eq.value), latest_fy, [mci, _in(eq)]))

    # FCF / EV multiples on latest FY
    if latest_fy:
        fcf = _fcf(idx, latest_fy)
        if fcf:
            out.append(_make("p_fcf", _div(mc, fcf[0]), latest_fy, [mci] + fcf[1]))
        td = _total_debt(idx, latest_fy)
        cash = _fact(idx, "CashAndCashEquivalents", latest_fy)
        ebitda = _ebitda(idx, latest_fy)
        if td and cash and ebitda:
            net_debt = td[0] - cash.value
            ev = mc + net_debt
            ev_inputs = [mci] + td[1] + [_in(cash)]
            out.append(_make("ev_ebitda", _div(ev, ebitda[0]), latest_fy, ev_inputs + ebitda[1]))
            if rev:
                out.append(_make("ev_sales", _div(ev, rev[0]), rev[1], ev_inputs + rev[2]))
        ni_fy = _fact(idx, "NetIncomeLoss", latest_fy)
        div = _fact(idx, "DividendsPaid", latest_fy)
        buyback = _fact(idx, "StockRepurchased", latest_fy)
        if div:
            out.append(_make("dividend_yield", _div(div.value, mc), latest_fy, [_in(div), mci]))
            if ni_fy:
                out.append(_make("payout_ratio", _div(div.value, ni_fy.value), latest_fy, [_in(div), _in(ni_fy)]))
        if buyback:
            out.append(_make("buyback_yield", _div(buyback.value, mc), latest_fy, [_in(buyback), mci]))
        if div and buyback:
            out.append(_make("total_shareholder_yield", _div(div.value + buyback.value, mc), latest_fy, [_in(div), _in(buyback), mci]))
    return out
```
In `compute_metrics`, after the period loop add: `out += _valuation(idx, quote)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/metrics.py tests/analytics/test_metrics.py
git commit -m "feat(analytics): TTM aggregates + valuation multiples"
```

---

## Task 11: Catalog ↔ compute name-coverage guard

**Files:**
- Modify: `tests/analytics/test_catalog.py`

Locks the invariant that every catalog name is produced by `compute_metrics` and vice versa, using a fixture exercising all families.

- [ ] **Step 1: Write the failing test**

Add to `tests/analytics/test_catalog.py`:
```python
def test_every_catalog_name_is_computable_and_vice_versa():
    from saturn.analytics.metrics import compute_metrics
    from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    rows = []
    concepts = [
        "Revenues", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
        "DepreciationAndAmortization", "StockholdersEquity", "Assets",
        "LiabilitiesCurrent", "AssetsCurrent", "Inventory",
        "CashAndCashEquivalents", "LongTermDebt", "DebtCurrent",
        "InterestExpense", "IncomeTaxExpenseBenefit", "CostOfRevenue",
        "CapitalExpenditures", "AccountsReceivableNetCurrent",
        "OperatingCashFlow", "WeightedAverageSharesDiluted",
        "EarningsPerShareDiluted", "DividendsPaid", "StockRepurchased",
    ]
    # Annual periods FY2022..FY2025 (CAGR/YoY) and 4 quarters of FY2025 (TTM/QoQ).
    for p in ["FY2022", "FY2023", "FY2024", "FY2025"]:
        for c in concepts:
            rows.append((c, p, 100.0 + len(c)))
    for q in ["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"]:
        for c in concepts:
            rows.append((c, q, 50.0 + len(c)))
    fund = Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=prov)
        for (c, p, v) in rows
    ])
    quote = Quote(price=100.0, market_cap=1_000_000.0, currency="USD", provenance=Provenance(source="yfinance"))

    produced = {m.name for m in compute_metrics(fund, quote)}
    assert produced == set(METRIC_CATALOG), (
        f"missing from compute: {set(METRIC_CATALOG) - produced}; "
        f"missing from catalog: {produced - set(METRIC_CATALOG)}"
    )
```

- [ ] **Step 2: Run test to verify it passes (or reveals a gap)**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_catalog.py::test_every_catalog_name_is_computable_and_vice_versa -v`
Expected: PASS. If it fails, the assertion message names the exact mismatch — fix the offending family or catalog entry, then re-run.

- [ ] **Step 3: (only if needed) reconcile names**

If a name is in the catalog but not produced (or vice versa), correct the spelling/registration in `catalog.py` or the relevant family in `metrics.py` so the sets match. No silent suppression.

- [ ] **Step 4: Run the whole analytics suite**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/analytics/test_catalog.py
git commit -m "test(analytics): guard catalog<->compute name coverage"
```

---

## Task 12: Attach derived metrics in build_dossier + mock

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create/append `tests/ingestion/test_dossier.py`:
```python
from saturn.ingestion.dossier import _mock_dossier, build_dossier


def test_mock_dossier_has_derived_metrics():
    d = _mock_dossier("NVDA")
    names = {m.name for m in d.derived_metrics}
    assert "net_margin" in names                       # computed from mock fundamentals
    assert all(m.provenance.source == "Saturn (derived)" for m in d.derived_metrics)


def test_build_dossier_attaches_metrics(monkeypatch):
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    fund = Fundamentals(facts=[
        FinancialFact(concept="Revenues", value=1000.0, unit="USD", fiscal_period="FY2025", provenance=prov),
        FinancialFact(concept="NetIncomeLoss", value=200.0, unit="USD", fiscal_period="FY2025", provenance=prov),
    ])
    quote = Quote(price=10.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_quote", lambda t, *, mock: quote)
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_edgar", lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"})
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_fred", lambda t: None)

    d = build_dossier("X")
    assert any(m.name == "net_margin" and m.fiscal_period == "FY2025" for m in d.derived_metrics)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: FAIL (`derived_metrics` empty).

- [ ] **Step 3: Implement**

In `saturn/ingestion/dossier.py`:
- Add import: `from saturn.analytics.metrics import compute_metrics`.
- In `_mock_dossier`, build the dossier into a variable, compute metrics, set the field, and return it. Change the trailing `return CompanyDossier(...)` to:
```python
    dossier = CompanyDossier(
        ...  # existing args unchanged
    )
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote)
    return dossier
```
- In `build_dossier`, replace the final `return CompanyDossier(...)` with:
```python
    dossier = CompanyDossier(
        ...  # existing args unchanged
    )
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote)
    return dossier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(dossier): compute and attach derived_metrics (real + mock)"
```

---

## Task 13: DERIVED METRICS block in the LLM context

**Files:**
- Modify: `saturn/workflows/equity_research.py` (`_company_context`)
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_equity_research.py`:
```python
def test_company_context_includes_derived_metrics():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.workflows.equity_research import _company_context

    ctx = _company_context(_mock_dossier("NVDA"))
    assert "DERIVED METRICS" in ctx
    assert "net_margin" in ctx
    assert "Saturn derived" in ctx       # provenance label shown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py::test_company_context_includes_derived_metrics -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py`, add a constant near the other `_CTX_*` constants:
```python
_CTX_MAX_METRIC_ANNUAL = 3
_CTX_MAX_METRIC_QUARTERS = 2
```
In `_company_context`, after the FUNDAMENTALS block (before FILING SECTIONS) add:
```python
    if dossier.derived_metrics:
        lines.append("\nDERIVED METRICS (computed by Saturn from as-reported data):")
        # bound display: recent annual + quarterly per metric, plus point-in-time
        by_name: dict[str, list] = {}
        for m in dossier.derived_metrics:
            by_name.setdefault(m.name, []).append(m)
        for name, metrics in by_name.items():
            annual = [m for m in metrics if (m.fiscal_period or "").startswith("FY")][:_CTX_MAX_METRIC_ANNUAL]
            quarterly = [m for m in metrics if (m.fiscal_period or "").startswith("Q")][:_CTX_MAX_METRIC_QUARTERS]
            other = [m for m in metrics if not (m.fiscal_period or "").startswith(("FY", "Q"))]
            for m in annual + quarterly + other:
                period = m.fiscal_period or "current"
                lines.append(
                    f"- {m.name} [{period}]: {m.value} ({m.formula}; source: Saturn derived)"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): surface derived metrics in the analyst context"
```

---

## Task 14: Key Metrics report section + methodology link + renumber

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_markdown_report.py`, update the section-header expectations in `test_render_has_all_sections` to the renumbered set and add a Key Metrics check:
```python
def test_render_has_all_sections():
    md = render(_sample_report())
    expected = [
        "## 1. Executive Summary",
        "## 2. Company Overview",
        "## 3. Business Segments",
        "## 4. Recent Market Performance",
        "## 5. Financial Snapshot",
        "## 6. Key Metrics",
        "## 7. Recent News and Catalysts",
        "## 8. Bull Thesis",
        "## 9. Bear Thesis",
        "## 10. Key Risks",
        "## 11. Valuation Discussion",
        "## 12. Open Questions",
        "## 13. Final View",
        "## 14. Macro Snapshot",
        "## 15. Material Events (SEC 8-K)",
        "## 16. Sources",
    ]
    for header in expected:
        assert header in md, f"missing: {header}"


def test_render_key_metrics_section():
    from saturn.models import DerivedMetric, MetricInput, Provenance
    report = _sample_report()
    report.company.derived_metrics = [
        DerivedMetric(name="net_margin", value=0.25, format="percent", fiscal_period="FY2024",
                      formula="NetIncomeLoss / Revenues",
                      inputs=[MetricInput(concept="NetIncomeLoss", fiscal_period="FY2024", value=1.0, source="SEC EDGAR")],
                      provenance=Provenance(source="Saturn (derived)")),
        DerivedMetric(name="pe_ratio", value=20.0, format="x", fiscal_period="TTM",
                      formula="market_cap / net_income_ttm",
                      inputs=[], provenance=Provenance(source="Saturn (derived)")),
    ]
    md = render(report)
    assert "## 6. Key Metrics" in md
    assert "net_margin" in md and "25.0%" in md          # percent formatting
    assert "20.0x" in md                                 # multiple formatting
    assert "docs/metrics.md" in md                       # methodology link
```
Also update `test_render_groups_financials_and_shows_events` and any other test asserting old section numbers (e.g. "## 14. Material Events" → "## 15. Material Events", "## 15. Sources" → "## 16. Sources", "## 16. Data Gaps" → "## 17. Data Gaps").

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -v`
Expected: FAIL (no Key Metrics section; old numbering).

- [ ] **Step 3: Implement**

In `saturn/reports/markdown_report.py`:
- Add a formatter helper near `_fmt_money`:
```python
def _fmt_metric(value: float, fmt: str) -> str:
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "x":
        return f"{value:.1f}x"
    if fmt == "currency":
        return _fmt_money(value)
    if fmt == "per_share":
        return f"${value:,.2f}"
    return f"{value:.2f}"   # ratio
```
- Add a bounding helper (mirrors `_select_report_facts` from PR #9) so the table
  stays compact — per metric, the most-recent 2 annual + 1 quarterly periods, plus
  any point-in-time entries (TTM / valuation whose period is not `FY*`/`Q*`).
  `compute_metrics` already emits annual then quarterly in descending recency, so
  slicing the grouped lists keeps the newest:
```python
_RPT_MAX_METRIC_ANNUAL = 2
_RPT_MAX_METRIC_QUARTERS = 1


def _select_report_metrics(metrics: list) -> list:
    by_name: dict[str, list] = {}
    order: list[str] = []
    for m in metrics:
        if m.name not in by_name:
            by_name[m.name] = []
            order.append(m.name)
        by_name[m.name].append(m)
    out: list = []
    for name in order:
        items = by_name[name]
        annual = [m for m in items if (m.fiscal_period or "").startswith("FY")]
        quarterly = [m for m in items if (m.fiscal_period or "").startswith("Q")]
        other = [m for m in items if not (m.fiscal_period or "").startswith(("FY", "Q"))]
        out += annual[:_RPT_MAX_METRIC_ANNUAL] + quarterly[:_RPT_MAX_METRIC_QUARTERS] + other
    return out
```
- Immediately after the Financial Snapshot block (after `out += [a.financial_snapshot, ""]`), insert the new section and renumber every subsequent `## N.` header by +1 (News→7, Bull→8, Bear→9, Key Risks→10, Valuation→11, Open Questions→12, Final View→13, Macro→14, Material Events→15, Sources→16, Data Gaps→17):
```python
    out += ["## 6. Key Metrics", ""]
    if c.derived_metrics:
        out.append("| Metric | Period | Value | Formula |")
        out.append("| --- | --- | --- | --- |")
        for m in _select_report_metrics(c.derived_metrics):
            out.append(
                f"| {m.name} | {m.fiscal_period or 'current'} | "
                f"{_fmt_metric(m.value, m.format)} | {m.formula} |"
            )
        out.append("")
        out.append(
            f"_Showing the most recent {_RPT_MAX_METRIC_ANNUAL} annual and "
            f"{_RPT_MAX_METRIC_QUARTERS} quarterly periods per metric. "
            "Definitions & formulas: docs/metrics.md_"
        )
    else:
        out.append("_No derived metrics available._")
    out.append("")
```

Note (spec deviation): spec §8 proposed a pivoted two-table layout; this plan
implements a single **bounded** long-format table for implementability. The pivot
can be a later presentation refinement — the data and provenance are unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): Key Metrics section + methodology link + renumber"
```

---

## Task 15: `saturn metrics` CLI command

**Files:**
- Modify: `saturn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (uses Typer's `CliRunner`, as the existing CLI tests do):
```python
def test_metrics_command_prints_reference():
    from typer.testing import CliRunner
    from saturn.cli import app

    result = CliRunner().invoke(app, ["metrics"])
    assert result.exit_code == 0
    assert "Saturn Metric Definitions" in result.stdout
    assert "gross_margin" in result.stdout


def test_metrics_command_write_regenerates_doc(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from saturn.cli import app
    import saturn.analytics.catalog as catalog

    target = tmp_path / "metrics.md"
    monkeypatch.setattr(catalog, "METRICS_DOC_PATH", target)
    result = CliRunner().invoke(app, ["metrics", "--write"])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == catalog.render_metrics_reference()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py::test_metrics_command_prints_reference -v`
Expected: FAIL (no `metrics` command).

- [ ] **Step 3: Implement**

In `saturn/cli.py`, add after the `doctor` command:
```python
@app.command()
def metrics(
    write: bool = typer.Option(False, "--write", help="Regenerate docs/metrics.md from the catalog."),
) -> None:
    """Print the derived-metric reference (or regenerate docs/metrics.md)."""
    import saturn.analytics.catalog as catalog

    content = catalog.render_metrics_reference()
    if write:
        catalog.METRICS_DOC_PATH.write_text(content, encoding="utf-8")
        typer.echo(f"Wrote {catalog.METRICS_DOC_PATH}")
    else:
        typer.echo(content)
```
(Import the module — not the names — so the test's `monkeypatch.setattr` on `METRICS_DOC_PATH` is honoured.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/cli.py tests/test_cli.py
git commit -m "feat(cli): saturn metrics [--write] reference command"
```

---

## Task 16: Full-suite verification + offline report smoke test

**Files:**
- Test: full suite

- [ ] **Step 1: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all tests; ~150+).

- [ ] **Step 2: Generate a mock report and eyeball Key Metrics**

Run: `.venv/Scripts/python.exe -m saturn.cli research NVDA --mock`
Then open `reports/NVDA_<today>.md` and confirm: a "## 6. Key Metrics" table renders with formatted values (percent/x/$), the methodology line "Metric definitions & formulas: docs/metrics.md" is present, and later sections are renumbered through "## 16. Sources".

- [ ] **Step 3: Confirm the metrics reference command works**

Run: `.venv/Scripts/python.exe -m saturn.cli metrics --write`
Then: `git status --short docs/metrics.md`
Expected: no diff (doc already in sync — drift guard holds).

- [ ] **Step 4: Commit any incidental fixes**

If Steps 1–3 surfaced fixes, commit them:
```bash
git add -A
git commit -m "test(analytics): full-suite verification fixes for derived metrics"
```

- [ ] **Step 5: Finish the branch**

Use **superpowers:finishing-a-development-branch** to complete (tests must pass first). Likely option: push and open a PR titled "Slice B — derived metrics layer".

---

## Notes for the implementer

- **DRY:** every metric's `format`/`formula` comes from `METRIC_CATALOG` via `_make`; never hardcode them at the call site.
- **YAGNI:** Q2/Q3 single-quarter cash flow by YTD subtraction is **out of scope** (spec §5) — `fcf` is emitted only for periods where both `OperatingCashFlow` and `CapitalExpenditures` facts exist (annual + Q1 post-PR #9). Do not fabricate the rest.
- **Provenance:** every `DerivedMetric` carries `Provenance(source="Saturn (derived)")` and an `inputs` list naming the source facts — this is what the future Critic will verify.
- **Negatives pass, zeros skip:** `_div` returns `None` on a zero/None denominator (metric omitted); negative numerators/denominators flow through (real signals like negative equity).

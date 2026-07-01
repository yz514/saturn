# Forward Metrics (Reverse-DCF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add forward/expectations metrics (2-stage reverse-DCF) derived only from current price + verified as-reported FCF — implied growth, expectations gap, implied return, fair-value range, margin of safety — tagged with a distinct `"Saturn (model)"` provenance.

**Architecture:** A new pure `saturn/analytics/forward.py` holds the DCF math, bisection solvers, and `compute_forward(fundamentals, quote)`. It reuses `metrics.py` helpers (`_index`/`_fact`/`_in`/`_annual_periods`) and resolves each output's format/formula from `METRIC_CATALOG`. `build_dossier` appends `compute_forward(...)` to `derived_metrics`; the report renders a "Forward / Expectations" sub-table and the LLM context a matching block, both filtered by `source == "Saturn (model)"`.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-23-forward-metrics-design.md`

---

## File Structure

- **Create:** `saturn/analytics/forward.py` (DCF + solvers + `compute_forward`); `tests/analytics/test_forward.py`
- **Modify:** `saturn/analytics/catalog.py` (7 entries + category); `docs/metrics.md` (regenerated); `saturn/ingestion/dossier.py` (append compute_forward, real + mock); `saturn/reports/markdown_report.py` (Forward sub-table + exclude model from main table); `saturn/workflows/equity_research.py` (Forward context block + exclude model from derived block); `tests/analytics/test_catalog.py` (CANONICAL + union coverage); touched report/context/dossier tests.

**Canonical forward metric names (7):** `implied_fcf_growth, expectations_gap, implied_return, reverse_dcf_fair_value_per_share, reverse_dcf_value_low_per_share, reverse_dcf_value_high_per_share, margin_of_safety`

**Invariant:** the catalog↔compute coverage guard must equal `set(compute_metrics names) ∪ set(compute_forward names)`. Catalog entries (Task 2) and `compute_forward` emission (Task 2) land together so the invariant holds at every commit.

---

## Task 1: forward.py DCF math core + solvers

**Files:**
- Create: `saturn/analytics/forward.py`, `tests/analytics/test_forward.py`

This task is pure math — no catalog, no metrics emitted, no integration. Nothing else in the suite is affected.

- [ ] **Step 1: Write the failing test**

Create `tests/analytics/test_forward.py`:
```python
from saturn.analytics.forward import (
    _dcf,
    _solve_implied_growth,
    _solve_implied_return,
)


def test_dcf_matches_hand_computation():
    # fcf0=100, g=0, r=10%, n=2, terminal g_t=2.5%
    # PV = 100/1.1 + 100/1.21 + (100*1.025/0.075)/1.21
    #    = 90.909 + 82.645 + 1129.477 = 1303.03
    assert abs(_dcf(100.0, 0.0, 0.10, n=2, g_t=0.025) - 1303.03) < 0.1


def test_dcf_monotonic_in_discount_rate():
    # higher discount rate -> lower present value
    assert _dcf(100.0, 0.10, 0.08) > _dcf(100.0, 0.10, 0.10) > _dcf(100.0, 0.10, 0.12)


def test_solve_implied_growth_round_trips():
    target = _dcf(100.0, 0.12, 0.10)
    g, converged = _solve_implied_growth(100.0, target, 0.10)
    assert converged and abs(g - 0.12) < 1e-4


def test_solve_implied_growth_clamps_when_out_of_range():
    # an enormous target implies more growth than the +60% ceiling
    huge = _dcf(100.0, 0.60, 0.10) * 100
    g, converged = _solve_implied_growth(100.0, huge, 0.10)
    assert not converged and abs(g - 0.60) < 1e-9   # clamped to upper bound


def test_solve_implied_return_round_trips():
    target = _dcf(100.0, 0.05, 0.09)
    r = _solve_implied_return(100.0, 0.05, target)
    assert r is not None and abs(r - 0.09) < 1e-4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_forward.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.analytics.forward'`.

- [ ] **Step 3: Implement the math core**

Create `saturn/analytics/forward.py`:
```python
"""Forward / expectations metrics via a 2-stage reverse-DCF on verified levered FCF.

Pure and offline; derived only from price + as-reported financials (no estimates).
"""

from __future__ import annotations

from datetime import date

from saturn.analytics.catalog import METRIC_CATALOG
from saturn.analytics.metrics import _annual_periods, _fact, _in, _index
from saturn.models import DerivedMetric, Fundamentals, MetricInput, Provenance, Quote

HORIZON_YEARS = 10
TERMINAL_GROWTH = 0.025
DISCOUNT_RATES = (0.08, 0.10, 0.12)   # low, base/mid, high
BASE_DISCOUNT = 0.10
GROWTH_CAP = 0.25                      # caps the fair-value growth assumption only
SOLVER_G_BOUNDS = (-0.50, 0.60)        # search range for the implied-growth solver
SOLVER_R_BOUNDS = (0.00, 0.50)         # search range for the implied-return solver

_MODEL = "Saturn (model)"
_ASSUMPTION = "Saturn (model assumption)"


def _dcf(fcf0: float, g: float, r: float, *, n: int = HORIZON_YEARS, g_t: float = TERMINAL_GROWTH) -> float:
    """Present value of a 2-stage FCF stream: grow at g for n years, then terminal
    growth g_t, all discounted at r. Requires r > g_t."""
    pv = 0.0
    fcf = fcf0
    for t in range(1, n + 1):
        fcf = fcf * (1 + g)
        pv += fcf / (1 + r) ** t
    terminal = fcf * (1 + g_t) / (r - g_t)   # fcf is FCF_n after the loop
    pv += terminal / (1 + r) ** n
    return pv


def _bisect(func, lo: float, hi: float, target: float, *, tol: float = 1e-7, iters: int = 200) -> float | None:
    """Find x in [lo, hi] with func(x) ~= target for a MONOTONIC func (either
    direction). Returns None if target is outside [func(lo), func(hi)]."""
    flo, fhi = func(lo), func(hi)
    if target < min(flo, fhi) or target > max(flo, fhi):
        return None
    increasing = flo < fhi
    for _ in range(iters):
        mid = (lo + hi) / 2
        fmid = func(mid)
        if abs(fmid - target) <= tol * max(1.0, abs(target)):
            return mid
        if (fmid < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _solve_implied_growth(fcf0: float, target: float, r: float) -> tuple[float, bool]:
    """The Stage-1 growth the price implies. Returns (g, converged); on out-of-range
    clamps to the nearer search bound with converged=False."""
    lo, hi = SOLVER_G_BOUNDS
    g = _bisect(lambda x: _dcf(fcf0, x, r), lo, hi, target)
    if g is not None:
        return (g, True)
    return (hi, False) if target > _dcf(fcf0, hi, r) else (lo, False)


def _solve_implied_return(fcf0: float, g: float, target: float) -> float | None:
    """The discount rate that equates DCF(g, r) to target, or None if out of range.
    Lower bound is held above the terminal growth (the terminal formula needs r > g_t)."""
    lo = max(SOLVER_R_BOUNDS[0], TERMINAL_GROWTH + 1e-4)
    hi = SOLVER_R_BOUNDS[1]
    return _bisect(lambda x: _dcf(fcf0, g, x), lo, hi, target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_forward.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/forward.py tests/analytics/test_forward.py
git commit -m "feat(forward): 2-stage reverse-DCF math core + bisection solvers"
```
(End the commit message with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.)

---

## Task 2: catalog entries + compute_forward + coverage guard

**Files:**
- Modify: `saturn/analytics/forward.py`, `saturn/analytics/catalog.py`, `docs/metrics.md`, `tests/analytics/test_forward.py`, `tests/analytics/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/analytics/test_forward.py`:
```python
from saturn.analytics.forward import compute_forward
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

_PROV = Provenance(source="SEC EDGAR")


def _ff(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=_PROV)
        for (c, p, v) in rows
    ])


def _quote(mc=1_000_000.0):
    return Quote(price=100.0, market_cap=mc, currency="USD", provenance=Provenance(source="yfinance"))


def _names(metrics):
    return {m.name for m in metrics}


def _positive_fcf_rows():
    # growing positive FCF (OCF - CapEx) across FY2022..FY2025 + shares
    rows = []
    for i, fy in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        rows += [("OperatingCashFlow", fy, 500.0 + 100 * i), ("CapitalExpenditures", fy, 50.0)]
        rows.append(("WeightedAverageSharesDiluted", fy, 100.0))
    return rows


def test_compute_forward_emits_all_seven_with_model_provenance():
    ms = compute_forward(_ff(_positive_fcf_rows()), _quote())
    assert _names(ms) == {
        "implied_fcf_growth", "expectations_gap", "implied_return",
        "reverse_dcf_fair_value_per_share", "reverse_dcf_value_low_per_share",
        "reverse_dcf_value_high_per_share", "margin_of_safety",
    }
    for m in ms:
        assert m.provenance.source == "Saturn (model)"
        assert m.fiscal_period == "model"
        assert any(i.concept == "market_cap" for i in m.inputs) or any("Cash" in i.concept or "Capital" in i.concept for i in m.inputs)


def test_compute_forward_fair_value_low_lt_high():
    ms = {m.name: m.value for m in compute_forward(_ff(_positive_fcf_rows()), _quote())}
    assert ms["reverse_dcf_value_low_per_share"] < ms["reverse_dcf_fair_value_per_share"] < ms["reverse_dcf_value_high_per_share"]


def test_compute_forward_expectations_gap_is_implied_minus_cagr():
    ms = {m.name: m.value for m in compute_forward(_ff(_positive_fcf_rows()), _quote())}
    # trailing 3y FCF CAGR from (500-50)=450 -> (800-50)=750: (750/450)**(1/3)-1
    cagr = (750.0 / 450.0) ** (1 / 3) - 1
    assert abs(ms["expectations_gap"] - (ms["implied_fcf_growth"] - cagr)) < 1e-9


def test_compute_forward_skips_on_nonpositive_fcf():
    rows = [("OperatingCashFlow", "FY2025", 100.0), ("CapitalExpenditures", "FY2025", 200.0)]  # FCF < 0
    assert compute_forward(_ff(rows), _quote()) == []


def test_compute_forward_skips_without_quote():
    assert compute_forward(_ff(_positive_fcf_rows()), None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_forward.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_forward'`.

- [ ] **Step 3: Add the catalog entries**

In `saturn/analytics/catalog.py`, add a new block to `_DEFS` immediately before the closing `]` (after the Quality & capital return block):
```python
    # Forward / Expectations  (reverse-DCF model; assumption-dependent)
    _d("implied_fcf_growth", "Forward / Expectations", "percent", "g s.t. 2-stage DCF(g, r=10%) = market_cap", "10-yr FCF growth the price implies.", "2-stage reverse-DCF on levered FCF (N=10, terminal 2.5%); FCFE-style (equity value vs market cap); clamped to search range [-50%, +60%]."),
    _d("expectations_gap", "Forward / Expectations", "percent", "implied_fcf_growth - trailing_3y_FCF_CAGR", "Implied growth vs the company's own 3-yr record.", "Positive = priced for acceleration; negative = priced below its track record."),
    _d("implied_return", "Forward / Expectations", "percent", "r s.t. 2-stage DCF(our growth, r) = market_cap", "Expected annual return at today's price given our growth view.", "Our growth = trailing 3-yr FCF CAGR clamped to [2.5%, 25%]."),
    _d("reverse_dcf_fair_value_per_share", "Forward / Expectations", "per_share", "2-stage DCF(our growth, r=10%) / diluted shares", "Mid-case reverse-DCF fair value per share.", "Our growth = trailing 3-yr FCF CAGR clamped to [2.5%, 25%]; FCFE-style."),
    _d("reverse_dcf_value_low_per_share", "Forward / Expectations", "per_share", "2-stage DCF(our growth, r=12%) / diluted shares", "Conservative reverse-DCF fair value per share.", "Higher discount rate."),
    _d("reverse_dcf_value_high_per_share", "Forward / Expectations", "per_share", "2-stage DCF(our growth, r=8%) / diluted shares", "Optimistic reverse-DCF fair value per share.", "Lower discount rate."),
    _d("margin_of_safety", "Forward / Expectations", "percent", "reverse_dcf_fair_value (mid) / market_cap - 1", "Model fair value vs price (>0 = cheap vs model).", "Uses mid-case equity value vs market cap."),
```
And append the category to `_CATEGORY_ORDER` (after `"Quality & capital return"`):
```python
    "Quality & capital return",
    "Forward / Expectations",
```

- [ ] **Step 4: Implement compute_forward**

Append to `saturn/analytics/forward.py`:
```python
def _fcf_at(idx, period) -> tuple[float, list[MetricInput]] | None:
    ocf = _fact(idx, "OperatingCashFlow", period)
    capex = _fact(idx, "CapitalExpenditures", period)
    if not ocf or not capex:
        return None
    return (ocf.value - capex.value, [_in(ocf), _in(capex)])


def _fcf_cagr_3y(idx, latest_fy: str) -> float | None:
    cur = _fcf_at(idx, latest_fy)
    prev = _fcf_at(idx, f"FY{int(latest_fy[2:]) - 3}")
    if not cur or not prev or cur[0] <= 0 or prev[0] <= 0:
        return None
    return (cur[0] / prev[0]) ** (1 / 3) - 1


def _assume(concept: str, value: float) -> MetricInput:
    return MetricInput(concept=concept, fiscal_period=None, value=value, source=_ASSUMPTION)


def _fmetric(name: str, value: float | None, inputs: list[MetricInput]) -> DerivedMetric | None:
    if value is None:
        return None
    d = METRIC_CATALOG[name]
    return DerivedMetric(
        name=name, value=value, format=d.fmt, fiscal_period="model",
        formula=d.formula, inputs=inputs,
        provenance=Provenance(source=_MODEL, as_of=date.today()),
    )


def compute_forward(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
    if quote is None or quote.market_cap is None:
        return []
    idx = _index(fundamentals)
    annual = _annual_periods(idx)
    if not annual:
        return []
    latest_fy = annual[0]
    fcf = _fcf_at(idx, latest_fy)
    if not fcf or fcf[0] <= 0:
        return []   # model meaningless for non-positive FCF — no fabrication
    fcf0 = fcf[0]
    mc = quote.market_cap
    mci = MetricInput(concept="market_cap", fiscal_period=None, value=mc, source=quote.provenance.source)
    base_assumptions = [
        _assume("discount_rate", BASE_DISCOUNT),
        _assume("terminal_growth", TERMINAL_GROWTH),
        _assume("horizon_years", float(HORIZON_YEARS)),
    ]
    out: list[DerivedMetric | None] = []

    g_imp, _converged = _solve_implied_growth(fcf0, mc, BASE_DISCOUNT)
    out.append(_fmetric("implied_fcf_growth", g_imp, fcf[1] + [mci] + base_assumptions))

    cagr = _fcf_cagr_3y(idx, latest_fy)
    if cagr is not None:
        g_fv = min(max(cagr, TERMINAL_GROWTH), GROWTH_CAP)
        cagr_in = _assume("trailing_3y_fcf_cagr", cagr)
        g_fv_in = _assume("growth_assumption", g_fv)
        out.append(_fmetric("expectations_gap", g_imp - cagr, fcf[1] + [mci] + base_assumptions + [cagr_in]))
        out.append(_fmetric("implied_return", _solve_implied_return(fcf0, g_fv, mc), fcf[1] + [mci, g_fv_in, cagr_in]))
        shares = _fact(idx, "WeightedAverageSharesDiluted", latest_fy)
        if shares and shares.value:
            low_r, mid_r, high_r = DISCOUNT_RATES   # 0.08, 0.10, 0.12
            sh = shares.value
            base_sh = fcf[1] + [_in(shares), g_fv_in, cagr_in]
            out.append(_fmetric("reverse_dcf_fair_value_per_share", _dcf(fcf0, g_fv, mid_r) / sh, base_sh + [_assume("discount_rate", mid_r)]))
            out.append(_fmetric("reverse_dcf_value_low_per_share", _dcf(fcf0, g_fv, high_r) / sh, base_sh + [_assume("discount_rate", high_r)]))
            out.append(_fmetric("reverse_dcf_value_high_per_share", _dcf(fcf0, g_fv, low_r) / sh, base_sh + [_assume("discount_rate", low_r)]))
            out.append(_fmetric("margin_of_safety", _dcf(fcf0, g_fv, mid_r) / mc - 1, fcf[1] + [mci, g_fv_in, cagr_in, _assume("discount_rate", mid_r)]))
    return [m for m in out if m]
```
Note: higher discount rate → lower value, so `_dcf(..., high_r)` is the LOW per-share value and `_dcf(..., low_r)` is the HIGH — the names map to value, not to the rate.

- [ ] **Step 5: Extend the coverage guard, regenerate the doc, and run**

In `tests/analytics/test_catalog.py`:
1. Add the 7 forward names to the `CANONICAL` set in `test_catalog_covers_canonical_names_exactly`.
2. Update `test_every_catalog_name_is_computable_and_vice_versa` to union `compute_forward`, and give the fixture positive growing FCF + shares so the forward metrics emit. Change the `produced` line and ensure these rows exist for every FY period in the fixture (set `OperatingCashFlow=500+50*i`, `CapitalExpenditures=50`, `WeightedAverageSharesDiluted=100` — overriding the generic `100+len(c)` values for those three concepts):
```python
def test_every_catalog_name_is_computable_and_vice_versa():
    from saturn.analytics.metrics import compute_metrics
    from saturn.analytics.forward import compute_forward
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
    for i, p in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        for c in concepts:
            rows.append((c, p, 100.0 + len(c)))
        # ensure positive, growing FCF and a clean share count for the forward model
        rows += [("OperatingCashFlow", p, 500.0 + 50.0 * i), ("CapitalExpenditures", p, 50.0),
                 ("WeightedAverageSharesDiluted", p, 100.0)]
    for q in ["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"]:
        for c in concepts:
            rows.append((c, q, 50.0 + len(c)))
    # dedupe (later rows win) so the FCF overrides take effect
    merged = {}
    for c, p, v in rows:
        merged[(c, p)] = v
    fund = Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=prov)
        for (c, p), v in merged.items()
    ])
    quote = Quote(price=100.0, market_cap=1_000_000.0, currency="USD", provenance=Provenance(source="yfinance"))

    produced = {m.name for m in compute_metrics(fund, quote)} | {m.name for m in compute_forward(fund, quote)}
    assert produced == set(METRIC_CATALOG), (
        f"missing from compute: {set(METRIC_CATALOG) - produced}; "
        f"extra in compute: {produced - set(METRIC_CATALOG)}"
    )
```
Then regenerate the doc and run:
```bash
.venv/Scripts/python.exe -m saturn.cli metrics --write
.venv/Scripts/python.exe -m pytest tests/analytics/ -q
```
Expected: doc rewritten; all analytics tests PASS (forward emission + coverage union + drift guard).

- [ ] **Step 6: Commit**

```bash
git add saturn/analytics/forward.py saturn/analytics/catalog.py docs/metrics.md tests/analytics/test_forward.py tests/analytics/test_catalog.py
git commit -m "feat(forward): compute_forward + catalog entries + union coverage guard"
```
(Append the Co-Authored-By trailer.)

---

## Task 3: Attach compute_forward in build_dossier + mock

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_dossier.py`:
```python
def test_build_dossier_attaches_forward_model_metrics(monkeypatch):
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    rows = []
    for i, fy in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        rows += [("OperatingCashFlow", fy, 500.0 + 50.0 * i), ("CapitalExpenditures", fy, 50.0),
                 ("WeightedAverageSharesDiluted", fy, 100.0)]
    fund = Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=prov)
        for (c, p, v) in rows
    ])
    quote = Quote(price=10.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_fred", lambda t: None)

    d = build_dossier(
        "X",
        quote_fn=lambda t, *, mock: quote,
        edgar_fn=lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"},
        fred_fn=lambda t: None,
    )
    model = [m for m in d.derived_metrics if m.provenance.source == "Saturn (model)"]
    assert any(m.name == "implied_fcf_growth" for m in model)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py::test_build_dossier_attaches_forward_model_metrics -v`
Expected: FAIL (no `"Saturn (model)"` metric present).

- [ ] **Step 3: Implement**

In `saturn/ingestion/dossier.py`:
- Add import: `from saturn.analytics.forward import compute_forward` (next to the existing `from saturn.analytics.metrics import compute_metrics`).
- In BOTH `_mock_dossier` and `build_dossier`, change the metric-attach line from:
```python
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote)
```
to:
```python
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote) + compute_forward(dossier.fundamentals, dossier.quote)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(dossier): attach forward reverse-DCF metrics (real + mock)"
```
(Append the Co-Authored-By trailer.)

---

## Task 4: Forward / Expectations report sub-table

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_markdown_report.py`:
```python
def test_render_forward_expectations_subtable():
    from saturn.models import DerivedMetric, MetricInput, Provenance
    report = _sample_report()
    report.company.derived_metrics = [
        DerivedMetric(name="net_margin", value=0.25, format="percent", fiscal_period="FY2024",
                      formula="NetIncomeLoss / Revenues", provenance=Provenance(source="Saturn (derived)")),
        DerivedMetric(name="implied_fcf_growth", value=0.18, format="percent", fiscal_period="model",
                      formula="g s.t. 2-stage DCF(g, r=10%) = market_cap",
                      inputs=[MetricInput(concept="market_cap", value=1.0, source="yfinance")],
                      provenance=Provenance(source="Saturn (model)")),
        DerivedMetric(name="margin_of_safety", value=-0.30, format="percent", fiscal_period="model",
                      formula="reverse_dcf_fair_value (mid) / market_cap - 1",
                      provenance=Provenance(source="Saturn (model)")),
    ]
    md = render(report)
    assert "Forward / Expectations" in md
    assert "implied_fcf_growth" in md and "18.0%" in md
    assert "margin_of_safety" in md and "-30.0%" in md
    # the model metrics are NOT duplicated into the main Key Metrics table
    assert md.count("implied_fcf_growth") == 1
    # the main table still shows the derived metric
    assert "net_margin" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py::test_render_forward_expectations_subtable -v`
Expected: FAIL (no Forward / Expectations section; model metrics leak into main table).

- [ ] **Step 3: Implement**

In `saturn/reports/markdown_report.py`, replace the Key Metrics section body (the block that starts `out += ["## 6. Key Metrics", ""]`) with a version that splits derived vs model and renders the sub-table:
```python
    out += ["## 6. Key Metrics", ""]
    _derived = [m for m in c.derived_metrics if m.provenance.source != "Saturn (model)"]
    _forward = [m for m in c.derived_metrics if m.provenance.source == "Saturn (model)"]
    if _derived:
        out.append("| Metric | Period | Value | Formula |")
        out.append("| --- | --- | --- | --- |")
        for m in _select_report_metrics(_derived):
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
    if _forward:
        out += ["### Forward / Expectations (model estimate)", ""]
        out.append("| Metric | Value | Formula |")
        out.append("| --- | --- | --- |")
        for m in _forward:
            out.append(f"| {m.name} | {_fmt_metric(m.value, m.format)} | {m.formula} |")
        out.append("")
        out.append(
            "_Reverse-DCF model (10-yr horizon, 2.5% terminal growth, 8/10/12% discount). "
            "Model estimate from price + as-reported FCF, not as-reported. See docs/metrics.md._"
        )
        out.append("")
```
(The rest of the `render` function — sections 7–16 — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -v`
Expected: PASS (new test + all existing report tests; the existing `test_render_key_metrics_section` still works because its metrics are `"Saturn (derived)"`).

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): Forward / Expectations sub-table (model metrics)"
```
(Append the Co-Authored-By trailer.)

---

## Task 5: Forward block in the LLM context

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_equity_research.py`:
```python
def test_company_context_includes_forward_block():
    from datetime import date as _date
    from saturn.models import CompanyDossier, DerivedMetric, Provenance
    from saturn.workflows.equity_research import _company_context

    d = CompanyDossier(ticker="X", name="X", generated_at=_date(2026, 6, 23))
    d.derived_metrics = [
        DerivedMetric(name="implied_fcf_growth", value=0.18, format="percent", fiscal_period="model",
                      formula="g s.t. 2-stage DCF(g, r=10%) = market_cap",
                      provenance=Provenance(source="Saturn (model)")),
    ]
    ctx = _company_context(d)
    assert "FORWARD / EXPECTATIONS" in ctx
    assert "implied_fcf_growth" in ctx
    assert "Saturn model" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py::test_company_context_includes_forward_block -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py`, inside `_company_context`, find the DERIVED METRICS block (`if dossier.derived_metrics:`). Replace its guard/iteration so it (a) only renders non-model metrics, and (b) appends a separate FORWARD block for model metrics. Concretely, change the opening lines of that block from:
```python
    if dossier.derived_metrics:
        lines.append("\nDERIVED METRICS (computed by Saturn from as-reported data):")
        # bound display: recent annual + quarterly per metric, plus point-in-time
        by_name: dict[str, list] = {}
        for m in dossier.derived_metrics:
            by_name.setdefault(m.name, []).append(m)
```
to:
```python
    _derived = [m for m in dossier.derived_metrics if m.provenance.source != "Saturn (model)"]
    _forward = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
    if _derived:
        lines.append("\nDERIVED METRICS (computed by Saturn from as-reported data):")
        # bound display: recent annual + quarterly per metric, plus point-in-time
        by_name: dict[str, list] = {}
        for m in _derived:
            by_name.setdefault(m.name, []).append(m)
```
(The body that follows — building `annual`/`quarterly`/`other` and appending lines — is unchanged.) Then, immediately after that block ends, add:
```python
    if _forward:
        lines.append("\nFORWARD / EXPECTATIONS (Saturn reverse-DCF model; assumption-dependent):")
        for m in _forward:
            lines.append(f"- {m.name}: {m.value} ({m.formula}; source: Saturn model)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS (new test + existing; the existing `test_company_context_includes_derived_metrics` still passes since the mock dossier's metrics are `"Saturn (derived)"`).

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): surface forward model metrics in analyst context"
```
(Append the Co-Authored-By trailer.)

---

## Task 6: Full-suite verification + offline smoke test

**Files:**
- Test: full suite

- [ ] **Step 1: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 2: Generate a mock report and eyeball the Forward sub-table**

Run: `.venv/Scripts/python.exe -m saturn.cli research NVDA --mock`
Open `reports/NVDA_<today>.md`. The mock dossier has FCF concepts only sparsely, so the Forward / Expectations sub-table may be absent (mock fundamentals lack positive multi-year FCF) — that's expected. Confirm the report still renders cleanly and the main Key Metrics table is unchanged. (A live `saturn research AVGO` is the real check, run separately.)

- [ ] **Step 3: Confirm the metrics doc is in sync**

Run: `.venv/Scripts/python.exe -m saturn.cli metrics --write`
Then: `git status --short docs/metrics.md`
Expected: no diff (drift guard holds).

- [ ] **Step 4: Commit any incidental fixes**

If Steps 1–3 surfaced fixes, commit them:
```bash
git add -A
git commit -m "test(forward): full-suite verification fixes"
```
(Append the Co-Authored-By trailer.)

- [ ] **Step 5: Finish the branch**

Use **superpowers:finishing-a-development-branch** (tests must pass first). Likely option: push and open a PR titled "Forward metrics (reverse-DCF)".

---

## Notes for the implementer

- **No scraped estimates:** every forward metric is derived from price + verified as-reported FCF. Do not add yfinance estimate fields — that's the next slice.
- **Provenance discipline:** forward metrics carry `source="Saturn (model)"` and `fiscal_period="model"`; their assumptions are recorded as `MetricInput`s with `source="Saturn (model assumption)"`. The static `formula` comes from the catalog (consistent with derived metrics); the runtime assumption *values* live in `inputs`, keeping the catalog/doc/number in sync.
- **No fabrication:** `compute_forward` returns `[]` when FCF ≤ 0 or there's no quote; `implied_return` is skipped (None) on solver non-convergence; `implied_fcf_growth` clamps to the search bound (never a bogus number) when the price implies more than ±the bound.
- **Value↔rate mapping:** the *low* per-share fair value uses the *high* discount rate and vice-versa — name by value, not by rate.

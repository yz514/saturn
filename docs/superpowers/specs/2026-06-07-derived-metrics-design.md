# Slice B — Derived Metrics Design

**Date:** 2026-06-07
**Status:** Approved (brainstorm) → ready for implementation plan
**Author:** Saturn (Claude) + user

## 1. Goal

Add a deterministic analytics layer that computes professional equity-research
metrics from the as-reported `CompanyDossier` (EDGAR fundamentals + yfinance
quote). Every metric carries its **formula** and the **exact source facts** it
consumed, so it is fully reproducible and (in Phase 1) Critic-verifiable. The
analyst LLM then *cites* these metrics instead of doing ungrounded arithmetic in
prose. No LLM, no network — pure functions over data already in the dossier.

This is the anti-hallucination depth win (closes much of F1 by construction): the
numbers in the report come from deterministic code with traceable lineage, not
from the model's head.

## 2. Scope

**In scope (this slice):** the comprehensive *trailing* professional metric set
below, computed from data we already ingest (plus two cheap EDGAR additions),
each tagged with formula + input lineage. Includes single-quarter cash flow
*where cleanly available* (annual FCF + Q1 single-quarter CF — see §5) and the
agreed "cheap wins" (shareholder yield, share-count change, effective tax rate,
earnings-quality pair, dividend/FCF coverage).

**Explicitly out of scope (named follow-on slices, sequenced):**
1. **Consensus / forward** — actual-vs-estimate, surprise, revisions, forward
   multiples. Needs a forward-estimates source we do not have; first task there
   is sourcing it honestly. *(the trading edge; planned next)*
2. **Price / market trading metrics** — beta, realized volatility, 52-week range
   position, momentum vs 50/200-DMA, relative strength vs index. Needs price
   *history* ingestion (yfinance provides it).
3. **Relative / peer & sector context** — multiples vs peers and sector medians.
   Needs peer-set selection + multi-company fetch.
4. **Cyclical normalization & reverse-DCF** — mid-cycle margin normalization;
   reverse-DCF to infer the growth the market is pricing in (expectations from
   the valuation side).

ROIC/ROCE use simple, documented approximations (see §6). EV is approximate
(ignores leases, preferred, minority interest); noted in the formula string.

## 3. New models (`saturn/models.py`)

```python
class MetricInput(BaseModel):
    """One source fact a derived metric consumed (for verification)."""
    concept: str
    fiscal_period: str | None
    value: float
    source: str                    # "SEC EDGAR", "yfinance"

class DerivedMetric(BaseModel):
    name: str                      # snake_case, e.g. "gross_margin"
    value: float
    format: str                    # "percent" | "ratio" | "currency" | "x" | "per_share"
    fiscal_period: str | None      # "FY2025" | "Q2 FY2026" | "TTM" | None (point-in-time)
    formula: str                   # "GrossProfit / Revenues"
    inputs: list[MetricInput] = Field(default_factory=list)
    provenance: Provenance         # source="Saturn (derived)", as_of=today
```

On `CompanyDossier`, add:
```python
derived_metrics: list[DerivedMetric] = Field(default_factory=list)
```
(flat list, same pattern as `material_events` / `news`).

`format` drives rendering: `percent` → `74.4%`; `ratio` → `1.85`; `x` → `12.3x`;
`currency` → `$17,755,000,000`; `per_share` → `$4.12`.

## 4. New module `saturn/analytics/metrics.py`

Pure and offline. Public entry point:

```python
def compute_metrics(
    fundamentals: Fundamentals | None,
    quote: Quote | None,
) -> list[DerivedMetric]:
    ...
```

Internals:
- `_index(fundamentals) -> dict[tuple[str, str], FinancialFact]` keyed by
  `(concept, fiscal_period)` for O(1) lookup.
- Period helpers reused/mirrored from existing code: parse `FY2024` and
  `Q2 FY2025`, list recent annual / quarterly periods in order.
- Small helpers: `_get(idx, concept, period) -> FinancialFact | None`,
  `_make(name, value, fmt, period, formula, inputs) -> DerivedMetric`.
- A registry of metric computations grouped by category (one function per metric
  family). Each returns a `DerivedMetric` or `None` (input missing / undefined).
- TTM and single-quarter-CF helpers (§5).

`compute_metrics` returns the flattened, non-`None` list.

`saturn/analytics/__init__.py` is added (new package).

### 4.1 Metric catalog = single source of truth (`saturn/analytics/catalog.py`)

The formula catalog (§6) lives in code **once**, as the authoritative source for
each metric's metadata, and drives both computation and the reference doc:

```python
@dataclass(frozen=True)
class MetricDef:
    name: str
    category: str            # "Profitability", "Returns", ...
    fmt: str                 # "percent" | "ratio" | "currency" | "x" | "per_share"
    formula: str             # "GrossProfit / Revenues"
    description: str         # one line, human-readable
    caveat: str | None = None  # e.g. "NOPAT approx = OpInc x (1 - eff. tax)."

METRIC_CATALOG: dict[str, MetricDef] = { ... }   # every metric in §6

def render_metrics_reference() -> str:
    """Render METRIC_CATALOG to the canonical docs/metrics.md markdown."""
```

- `compute_metrics` pulls each emitted `DerivedMetric`'s `format` and `formula`
  from `METRIC_CATALOG[name]` (never hardcoded at the call site), so the number,
  the report, and the doc all show the *same* formula string by construction.
- `docs/metrics.md` is **generated** from `render_metrics_reference()` and
  committed, with a header noting "generated — do not edit by hand; run
  `saturn metrics --write`".
- CLI: `saturn metrics` prints the reference to stdout; `saturn metrics --write`
  regenerates `docs/metrics.md`.
- A drift-guard test (see §10) asserts the committed `docs/metrics.md` equals
  `render_metrics_reference()`, and that catalog names and computed-metric names
  match exactly (no orphan metric, no undocumented metric). Drift becomes
  impossible to merge.

## 5. Period coverage, TTM, and single-quarter cash flow

**Level metrics** (margins, returns, liquidity, leverage, efficiency, per-share)
are computed for **every annual and quarterly period present in the fundamentals**
(EDGAR provides ~4 FY + ~8 Q), so the dossier holds the full trend set; the
context and report **bound the display** (recent 3 FY + a small quarterly set),
mirroring the as-reported facts table. A metric is emitted for a period only when
its inputs exist for that exact period.

**Growth metrics:**
- YoY: annual `FY[t] / FY[t-1] - 1`; quarterly `Q[t] / Q[same quarter, t-1] - 1`.
- 3-yr CAGR (latest): `(FY[t] / FY[t-3])^(1/3) - 1`.
- QoQ (latest, sequential): `Q[t] / Q[t-1] - 1`.

**TTM aggregates** (income-statement flows only — single quarters are correct
post-PR #9): `revenue_ttm`, `net_income_ttm`, `eps_ttm` = sum of the last 4
single-quarter values, emitted as `DerivedMetric`s with `fiscal_period="TTM"`.
If 4 clean consecutive single quarters are unavailable, the dependent valuation
multiples fall back to the latest FY value and label their period accordingly
(`"FY2025"`), never silently mixing. EBITDA and FCF have **no** clean single
quarters (cash-flow / D&A items are YTD-only), so valuation multiples that use
them (`ev_ebitda`, `p_fcf`) use the **latest FY** value.

**Single-quarter cash flow — what Slice B emits (and what it doesn't):**
PR #9 deliberately drops YTD-only quarterly cash-flow durations from the
as-reported `Fundamentals`, so the dossier currently exposes, for cash-flow
concepts: **annual** values (all years) and **Q1** values (Q1 YTD *is* the
single quarter, so PR #9 retains it). Slice B therefore emits, from data already
in the dossier:
- annual FCF for every year (`OperatingCashFlow − CapitalExpenditures`),
- Q1 single-quarter OCF / CapEx / FCF.

Full **Q2/Q3 single-quarter** cash flow by YTD subtraction
(`OCF_Qn = OCF_YTD_n − OCF_YTD_{n-1}`) is **deferred to a focused follow-up**: it
requires re-exposing the raw YTD cash-flow values that PR #9 drops (under honest
YTD labels, with matching report/context handling), which is a bounded change of
its own and would otherwise partly reverse PR #9. Slice B does not fabricate
these — it emits only the cleanly-available annual + Q1 figures above.

## 6. Metric catalog

This table is the human view of `METRIC_CATALOG` (§4.1); the code holds the same
set as the single source of truth, and `docs/metrics.md` is generated from it.

`fmt` legend: % = percent, R = ratio, $ = currency, x = multiple, /sh = per_share.
"TotalDebt" = `LongTermDebt + DebtCurrent` (DebtCurrent optional; if absent, use
`LongTermDebt` and note the approximation in the formula string). "EBITDA" =
`OperatingIncomeLoss + DepreciationAndAmortization`. "PretaxIncome" =
`NetIncomeLoss + IncomeTaxExpenseBenefit`.

### Profitability
| name | fmt | formula |
|---|---|---|
| gross_margin | % | GrossProfit / Revenues |
| operating_margin | % | OperatingIncomeLoss / Revenues |
| net_margin | % | NetIncomeLoss / Revenues |
| ebitda_margin | % | (OperatingIncomeLoss + DepreciationAndAmortization) / Revenues |
| fcf_margin | % | FCF / Revenues |

### Returns on capital
| name | fmt | formula |
|---|---|---|
| roe | % | NetIncomeLoss / StockholdersEquity |
| roa | % | NetIncomeLoss / Assets |
| roic | % | (OperatingIncomeLoss × (1 − effective_tax_rate)) / (TotalDebt + StockholdersEquity)  *(approx NOPAT / invested capital)* |
| roce | % | OperatingIncomeLoss / (Assets − LiabilitiesCurrent) |

### Liquidity
| name | fmt | formula |
|---|---|---|
| current_ratio | R | AssetsCurrent / LiabilitiesCurrent |
| quick_ratio | R | (AssetsCurrent − Inventory) / LiabilitiesCurrent |
| cash_ratio | R | CashAndCashEquivalents / LiabilitiesCurrent |

### Leverage / solvency
| name | fmt | formula |
|---|---|---|
| debt_to_equity | R | TotalDebt / StockholdersEquity |
| debt_to_assets | R | TotalDebt / Assets |
| net_debt | $ | TotalDebt − CashAndCashEquivalents |
| net_debt_to_ebitda | x | net_debt / EBITDA |
| interest_coverage | x | OperatingIncomeLoss / InterestExpense |

### Efficiency
| name | fmt | formula |
|---|---|---|
| asset_turnover | x | Revenues / Assets |
| inventory_turnover | x | CostOfRevenue / Inventory |
| capex_intensity | % | CapitalExpenditures / Revenues |
| days_sales_outstanding | R (days) | AccountsReceivableNetCurrent / Revenues × 365  *(annual only)* |

### Cash generation
| name | fmt | formula |
|---|---|---|
| fcf | $ | OperatingCashFlow − CapitalExpenditures |
| fcf_conversion | % | FCF / NetIncomeLoss  *(earnings quality)* |

### Growth
| name | fmt | formula |
|---|---|---|
| revenue_growth_yoy | % | Rev[t] / Rev[t−1] − 1 |
| eps_growth_yoy | % | EPS[t] / EPS[t−1] − 1 |
| fcf_growth_yoy | % | FCF[t] / FCF[t−1] − 1 |
| revenue_cagr_3y | % | (Rev[t] / Rev[t−3])^(1/3) − 1 |
| eps_cagr_3y | % | (EPS[t] / EPS[t−3])^(1/3) − 1 |
| revenue_growth_qoq | % | Rev[Q] / Rev[Q−1] − 1 |

### Per-share
| name | fmt | formula |
|---|---|---|
| fcf_per_share | /sh | FCF / WeightedAverageSharesDiluted |
| book_value_per_share | /sh | StockholdersEquity / WeightedAverageSharesDiluted |

### Valuation (trailing; needs Quote price/market_cap)
TTM where available, else latest FY (period labeled accordingly).
| name | fmt | formula |
|---|---|---|
| pe_ratio | x | market_cap / NetIncome_ttm |
| ps_ratio | x | market_cap / Revenue_ttm |
| pb_ratio | x | market_cap / StockholdersEquity |
| p_fcf | x | market_cap / FCF |
| ev_ebitda | x | (market_cap + net_debt) / EBITDA |
| ev_sales | x | (market_cap + net_debt) / Revenue_ttm |
| earnings_yield | % | NetIncome_ttm / market_cap |
| dividend_yield | % | DividendsPaid / market_cap |
| payout_ratio | % | DividendsPaid / NetIncomeLoss |

### Cheap wins (no new data beyond §7)
| name | fmt | formula |
|---|---|---|
| effective_tax_rate | % | IncomeTaxExpenseBenefit / PretaxIncome |
| buyback_yield | % | StockRepurchased / market_cap |
| total_shareholder_yield | % | (DividendsPaid + StockRepurchased) / market_cap |
| share_count_change_yoy | % | Shares[t] / Shares[t−1] − 1  *(dilution signal)* |
| dividend_coverage | x | FCF / DividendsPaid |
| accruals_ratio | % | (NetIncomeLoss − OperatingCashFlow) / Assets  *(earnings quality; high = lower quality)* |

## 7. EDGAR input additions

Add two entries to `EDGAR_CONCEPTS` in `saturn/ingestion/edgar.py` (one line
each), enabling net debt / total debt and DSO:
```python
"DebtCurrent": {"unit": "USD", "tags": ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"]},
"AccountsReceivableNetCurrent": {"unit": "USD", "tags": ["AccountsReceivableNetCurrent"]},
```
These are instant balance-sheet concepts and flow through the existing
period-selection logic (PR #9). Everything else uses concepts already ingested.

## 8. Integration

**Computation site:** in `saturn/ingestion/dossier.py`, after the dossier's
`fundamentals` and `quote` are assembled (real path *and* `_mock_dossier`), call
`compute_metrics(...)` and set `dossier.derived_metrics`. It is a pure
post-processing step — **not** routed through `route_to_source` (no I/O, cannot
fail a source). If it returns `[]`, the rest of the pipeline is unaffected.

**LLM context** (`_company_context` in `equity_research.py`): add a
"DERIVED METRICS" block after FUNDAMENTALS, grouped by metric name, rendered as
`name [period]: value (formula; source: Saturn derived)`. Bounded to recent 3 FY
+ 2 Q per metric (prompt-budget control) plus all point-in-time/TTM entries.

**Report** (`markdown_report.py`): a new **"6. Key Metrics"** section after
Financial Snapshot (sections renumber down). Two compact tables:
- **Trailing Metrics** — pivoted: rows = level/growth metrics, columns = a small
  fixed recency set `[FY-2, FY-1, FY (latest), Latest Q]`, sparse cells blank.
- **Valuation (current)** — point-in-time / TTM multiples: `Metric · Value ·
  Formula`.
Both bounded for readability; a transparency note states the periods shown. The
dossier retains the full set. A line links the canonical methodology:
"Metric definitions & formulas: docs/metrics.md".

## 9. Error handling

- Missing input or **zero** denominator → metric omitted (return `None`); never
  raises, never divides by zero.
- **Negative** values pass through unchanged — negative equity → negative D/E,
  losses → negative margin are real signals (MSTR's negative-equity quarter is an
  explicit test case).
- `compute_metrics` is total: any internal lookup miss yields `None` for that
  metric only. The dossier always gets a (possibly empty) list.

## 10. Testing (TDD, fully offline)

Pure unit tests in `tests/analytics/test_metrics.py` over tiny synthetic
`Fundamentals` / `Quote`:
- One test per metric family: correct value on known inputs.
- Missing-input skip; zero-denominator skip; negative passthrough.
- YoY (annual + quarterly same-quarter), 3-yr CAGR, QoQ selection correctness.
- TTM sum from 4 single quarters; FY fallback when quarters incomplete.
- Annual FCF for each year; Q1 single-quarter OCF/CapEx/FCF emitted; assert no
  Q2/Q3 single-quarter CF is fabricated (deferred per §5).
- `format` field correctness per metric.

Catalog & doc generation (`tests/analytics/test_catalog.py`):
- `render_metrics_reference()` output equals the committed `docs/metrics.md`
  (drift guard — fails if the catalog changed without regenerating the doc).
- Catalog names and the set of names `compute_metrics` can emit match exactly
  (no orphan metric, no undocumented metric).
- Every `DerivedMetric`'s `format`/`formula` come from `METRIC_CATALOG`.

Plus:
- `tests/test_equity_research.py`: context includes the DERIVED METRICS block
  with formula + provenance.
- `tests/test_markdown_report.py`: Key Metrics section renders; sections
  renumber; bounded with note.
- `tests/ingestion/` (or dossier test): `build_dossier` / `_mock_dossier`
  populates `derived_metrics`; EDGAR parses the two new concepts.

## 11. File structure summary

- **Create:** `saturn/analytics/__init__.py`, `saturn/analytics/metrics.py`,
  `saturn/analytics/catalog.py` (MetricDef, METRIC_CATALOG,
  render_metrics_reference), `docs/metrics.md` (generated, committed),
  `tests/analytics/__init__.py`, `tests/analytics/test_metrics.py`,
  `tests/analytics/test_catalog.py`
- **Modify:** `saturn/models.py` (MetricInput, DerivedMetric, dossier field);
  `saturn/ingestion/edgar.py` (two concepts); `saturn/ingestion/dossier.py`
  (compute + attach, real + mock); `saturn/workflows/equity_research.py`
  (context block); `saturn/reports/markdown_report.py` (Key Metrics section +
  methodology link + renumber); `saturn/cli.py` (`metrics` command); existing
  tests for the touched renderers/context.

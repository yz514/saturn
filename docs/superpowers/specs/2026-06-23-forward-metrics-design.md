# Forward Metrics (Reverse-DCF) Design

**Date:** 2026-06-23
**Status:** Approved (brainstorm) → ready for implementation plan
**Author:** Saturn (Claude) + user

## 1. Goal

Add **forward-looking / expectations metrics** derived *only* from the current
price plus our **verified** as-reported financials — no scraped analyst estimates,
so there is nothing to contaminate (a live yfinance probe showed AVGO's scraped
consensus was implausible: +170% revenue). The core question answered:

> *What is today's price implying, and how does that compare to what the company
> has actually done?*

These are **model** outputs (assumption-dependent), tagged distinctly from the
pure-arithmetic derived metrics so their different epistemic status is explicit.

This is the first of the forward slices; **yfinance consensus** (forward P/E,
analyst estimates, price targets, surprise) is a separate later slice where the
first task is sourcing-quality validation.

## 2. The model — 2-stage reverse-DCF on levered FCF

Hypergrowth names (AI/semis) are the typical targets; a single-stage perpetual-
growth model badly understates them, so we use two stages.

- **Base flow:** latest fiscal-year **FCF** = `OperatingCashFlow − CapitalExpenditures`
  (already ingested and verified). Levered FCF discounted at the cost of equity →
  implied **equity** value, compared to **market cap**. This is an FCFE-style
  simplification (treats levered FCF as cash flow to equity, ignores changing
  capital structure); documented as such. An EV-based (unlevered FCFF) model is a
  future refinement (§8).
- **Stage 1:** FCF grows at rate `g` for `N = 10` years (discounted).
- **Stage 2 (terminal):** `TV_N = FCF_N · (1 + g_t) / (r − g_t)`, discounted from
  year `N`. Terminal growth `g_t = 2.5%`.
- **Discount-rate grid:** `r ∈ {8%, 10%, 12%}`, base `r = 10%`.
- **Intrinsic equity value** at `(g, r)`:
  `DCF(g, r) = Σ_{t=1..N} FCF·(1+g)^t / (1+r)^t  +  TV_N / (1+r)^N`.
- **Growth assumption for fair value** = trailing **3-year FCF CAGR**
  (`(FCF[FY] / FCF[FY−3])^(1/3) − 1`), **clamped to `[g_t, 0.25]`** so a low base
  can't yield absurd projections. The clamp and the raw value are recorded in
  provenance.
- `r > g_t` is always enforced (terminal formula requires it).

### Default assumptions (module constants in `forward.py`)
```python
HORIZON_YEARS = 10
TERMINAL_GROWTH = 0.025
DISCOUNT_RATES = (0.08, 0.10, 0.12)   # low, base/mid, high
BASE_DISCOUNT = 0.10
GROWTH_CAP = 0.25                      # caps the FAIR-VALUE growth assumption only
SOLVER_G_BOUNDS = (-0.50, 0.60)       # search range for the implied-growth solver
SOLVER_R_BOUNDS = (0.00, 0.50)        # search range for the implied-return solver
```
Note the two distinct uses: `GROWTH_CAP` bounds the growth rate WE project for fair
value (so a low base can't yield absurd projections); `SOLVER_G_BOUNDS` is the wide
search range for *reading* the growth the price implies (we want the real number for
hypergrowth names, not a clamp). Each metric records the actual values it used;
constants are easy to tune in one place. User-configurable assumptions are out of
scope for v1.

## 3. Output metrics

New catalog category **"Forward / Expectations"**, all with
`provenance.source = "Saturn (model)"` and `fiscal_period = "model"`.

| name | fmt | meaning |
|---|---|---|
| `implied_fcf_growth` | percent | The 10-yr Stage-1 FCF growth the price bakes in: solve `g` s.t. `DCF(g, r=10%) = market_cap`. |
| `expectations_gap` | percent | `implied_fcf_growth − trailing_3y_FCF_CAGR` — the headline signal (positive = priced for acceleration vs its own record; negative = priced below its track record). |
| `implied_return` | percent | The discount rate `r*` that equates `DCF(our_growth, r*) = market_cap` — expected annual return at today's price given our growth view. |
| `reverse_dcf_fair_value_per_share` | per_share | Mid case: `DCF(our_growth, r=10%) / shares`. |
| `reverse_dcf_value_low_per_share` | per_share | Conservative: `DCF(our_growth, r=12%) / shares`. |
| `reverse_dcf_value_high_per_share` | per_share | Optimistic: `DCF(our_growth, r=8%) / shares`. |
| `margin_of_safety` | percent | `reverse_dcf_fair_value (mid) / market_cap − 1` (>0 = cheap vs model). Uses total equity value vs market cap (equivalent to per-share vs price). |

`shares` = latest-FY `WeightedAverageSharesDiluted`. (Per-share fair values use the
same total `DCF` equity value divided by shares; `margin_of_safety` compares the
total mid `DCF` to `market_cap`, which is equivalent.)

## 4. Provenance

Each output is a `DerivedMetric`:
- `provenance.source = "Saturn (model)"` (distinct from `"Saturn (derived)"`) —
  signals "depends on assumptions; treat differently from as-reported-derived."
- `fiscal_period = "model"`.
- `formula` string captures the model + the exact assumptions used, e.g.
  `"2-stage DCF; FCF=$26.9B (FY2025), N=10, g_t=2.5%, r=10%, growth=24.4% (3y FCF CAGR, capped to 25%)"`.
- `inputs` = the FCF source fact(s) it consumed + a `market_cap` `MetricInput`
  (and the shares fact for per-share outputs).

The recency window (`_drop_stale`) keeps these (period `"model"` has no FY/Q year),
so they always surface when valid.

## 5. Module & integration

- **Create `saturn/analytics/forward.py`** — pure, offline. Public entry point:
  ```python
  def compute_forward(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
      ...
  ```
  Internals: `_dcf(fcf0, g, r, *, n=HORIZON_YEARS, g_t=TERMINAL_GROWTH) -> float`
  (the 2-stage present value); `_solve_implied_growth(fcf0, target, r) -> float | None`
  and `_solve_implied_return(fcf0, g, target) -> float | None` (bisection, bounded,
  return `None` on non-convergence); a `_latest_fy` helper and a `_fcf_cagr_3y`
  helper. Reuses `MetricInput`/`DerivedMetric`/`Provenance` from `models.py` and the
  same `_make`-style construction (format/formula resolved from the catalog).
- **Catalog:** add the seven entries to `METRIC_CATALOG` (in `catalog.py`) under a
  new `"Forward / Expectations"` category, appended to `_CATEGORY_ORDER`. Caveats
  document the assumptions and the FCFE simplification. `docs/metrics.md`
  regenerated.
- **Dossier:** `build_dossier` (and `_mock_dossier`) set
  `dossier.derived_metrics = compute_metrics(...) + compute_forward(...)`.
- **Report:** a new **"Forward / Expectations"** sub-table after Key Metrics,
  rendering the model metrics (filtered by `source == "Saturn (model)"`), with a
  note stating the base assumptions and "model estimate, not as-reported."
- **Context:** `_company_context` surfaces the model metrics in a `FORWARD /
  EXPECTATIONS (Saturn model)` block so the analyst can cite them with their
  assumptions.
- **Coverage test:** extend the catalog↔compute guard to assert
  `set(compute_metrics names) ∪ set(compute_forward names) == set(METRIC_CATALOG)`.

## 6. Validity & edge cases

`compute_forward` returns `[]` (no fabrication) when:
- **FCF ≤ 0** at the latest FY (model meaningless for a cash-burning company).
- **No quote / `market_cap is None`** (nothing to invert against).
- **Trailing 3-yr FCF CAGR uncomputable** (need FCF at FY and FY−3, both > 0) —
  affects only the fair-value/`implied_return`/`expectations_gap` outputs; if the
  CAGR is missing, those are skipped but `implied_fcf_growth` (which doesn't need
  it) is still emitted.
- **Solver out of range:** `_solve_implied_growth` searches `SOLVER_G_BOUNDS`
  `(-50%, +60%)`. If `market_cap` exceeds `DCF(g=upper_bound, r=10%)` the price
  implies more than the search ceiling; emit `implied_fcf_growth` at the bound with a
  `"≥ 60%"` caveat in the formula (rather than a bogus number); symmetrically `"≤
  -50%"` at the floor. `_solve_implied_return` searches `SOLVER_R_BOUNDS` `(0%, 50%)`
  and is skipped (returns `None`) if no `r` in range equates `DCF(our_growth, r)` to
  `market_cap`.

Division/most arithmetic guarded as in the existing layer. `r > g_t` enforced by
construction (all `DISCOUNT_RATES` > `TERMINAL_GROWTH`); the fair-value growth
assumption is clamped to `[g_t, GROWTH_CAP]`.

## 7. Testing (TDD, fully offline)

`tests/analytics/test_forward.py`:
- `_dcf` matches a hand-computed 2-stage present value for known inputs.
- `_solve_implied_growth` recovers a known `g` (round-trip: `DCF(g)` → solve →
  `g`), within tolerance.
- `_solve_implied_return` recovers a known `r`.
- Fair value is monotonic in `r` (low < mid < high).
- `expectations_gap = implied_fcf_growth − trailing_3y_FCF_CAGR` arithmetic.
- `margin_of_safety` sign/value vs a known fair value.
- **Skips:** negative FCF → `[]`; no quote → `[]`; missing CAGR → fair-value
  outputs absent but `implied_fcf_growth` present; non-convergence → clamped with
  caveat, not a bogus value.
- Provenance: `source == "Saturn (model)"`, assumptions present in `formula`,
  `inputs` include FCF + market_cap.

Plus:
- `tests/analytics/test_catalog.py`: the extended union coverage guard; doc drift
  guard still green after regeneration.
- `tests/ingestion/test_dossier.py`: `derived_metrics` includes a `"Saturn (model)"`
  entry for a fixture with positive FCF + quote.
- `tests/test_markdown_report.py` / `tests/test_equity_research.py`: the Forward /
  Expectations sub-table and context block render.

## 8. Out of scope (named follow-ons)

- **yfinance consensus** — forward P/E, forward EPS, consensus revenue/EPS
  estimates, analyst price targets, recommendation, earnings surprise, estimate
  revisions — as a best-effort `"estimate"`-provenance source. The first task there
  is validation/sourcing quality (the live AVGO probe was contaminated).
- EV-based (unlevered FCFF) reverse-DCF with net-debt bridge.
- Multi-scenario / user-configurable assumptions; Monte-Carlo sensitivity.
- A reverse-DCF on **owner earnings** or **revenue→margin** build-up instead of FCF.

## 9. File structure summary

- **Create:** `saturn/analytics/forward.py`, `tests/analytics/test_forward.py`
- **Modify:** `saturn/analytics/catalog.py` (7 entries + category + regenerate
  `docs/metrics.md`); `saturn/ingestion/dossier.py` (append `compute_forward`,
  real + mock); `saturn/reports/markdown_report.py` (Forward / Expectations
  sub-table); `saturn/workflows/equity_research.py` (context block);
  `tests/analytics/test_catalog.py` (union coverage); `docs/metrics.md`
  (regenerated); touched report/context tests.

# Driver Model (Slice 1) — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** the alpha frame (v1.0/v1.1/v1.2 + alpha-thesis auto-repair, all merged to `main`).

## 1. Goal & honest framing

Give the alpha frame a **quantitative spine**: Saturn computes its own bottom-up forward EPS
from as-reported data (revenue → margin → EPS) and decomposes the gap versus the Street's number
by driver. Today the alpha scenarios rest on LLM-supplied assumptions; this makes the variant view
rest on a transparent, deterministic estimate instead.

**Honest boundary:** a driver model does not forecast better than the Street. Its value is
**transparency + attribution** — a number whose math you can see, and a decomposition of *why* it
differs from consensus ("the Street's $12.40 needs +10% revenue growth; your trailing trend is
6%"). The base case is a **mechanical trailing-trend baseline**, explicitly labeled as such, not a
forward judgment.

This is **Slice 1** of a larger driver-model effort. Deferred to later slices: segment build-up
(Slice 2), sector-specific driver libraries (Slice 3), consensus-revenue ingestion, an FCF bridge,
multi-year horizons, and any change to the stance derivation.

## 2. The bridge (deterministic, EPS, 1-year forward)

Pure math, no LLM. Mirrors `saturn/analytics/forward.py`'s helper style (`_index`, `_ttm`, `_fact`,
`_annual_periods`, `_fcf_cagr_3y`).

- **Base revenue** = TTM revenue via `_ttm(idx, "Revenues")`.
- **Trailing growth `g`** = 3-year revenue CAGR from FY `Revenues` facts (a `_revenue_cagr_3y`
  helper analogous to `_fcf_cagr_3y`).
- **Trailing net margin `m`** = TTM `NetIncomeLoss` / TTM `Revenues` (both via `_ttm`).
- **Shares `s`** = latest-FY `WeightedAverageSharesDiluted` (`_fact(idx, ..., latest_fy)`).
- **Saturn forward EPS** = `TTM_revenue × (1 + g) × m / s`.

Horizon = **1-year-forward (NTM)** to match the yfinance consensus forward-EPS horizon (documented
as approximate).

## 3. Consensus decomposition + gap (two-lens)

`ConsensusSnapshot` has **forward EPS but not consensus revenue**, so a single EPS number cannot be
uniquely split into growth vs margin. The honest decomposition is **two lenses**:

- **Lens A — hold margin at trailing `m`:** growth the Street needs =
  `(consensus_eps × s / m) / TTM_revenue − 1`. → "consensus needs +X% revenue growth (your trend
  is g)."
- **Lens B — hold growth at trailing `g`:** margin the Street needs =
  `consensus_eps × s / (TTM_revenue × (1 + g))`. → "…or a Y% net margin (your trailing is m)."
- **Gap** = `saturn_eps − consensus_eps` (absolute and %).

*(Deferred: ingesting consensus revenue would enable a clean 2-factor growth-vs-margin waterfall.)*

## 4. Data model & where it lives

New `class DriverModel(BaseModel)` in `saturn/models.py`:

```python
class DriverModel(BaseModel):
    horizon: str = "NTM"
    saturn_eps: float                       # Saturn's trailing-trend forward EPS
    trailing_revenue_growth: float          # g
    trailing_net_margin: float              # m
    shares: float
    consensus_eps: float | None = None
    eps_gap: float | None = None            # saturn_eps - consensus_eps
    eps_gap_pct: float | None = None
    consensus_implied_growth: float | None = None   # Lens A
    consensus_implied_margin: float | None = None    # Lens B
    low_confidence: bool = False
    caveats: list[str] = Field(default_factory=list)
    provenance: Provenance                   # source="Saturn (model)"
```

`CompanyDossier` gains `driver_model: DriverModel | None = None`.

New `saturn/analytics/driver.py`: `compute_driver_model(fundamentals, quote, consensus) -> DriverModel | None`.
It soft-returns `None` when the required inputs (TTM revenue, TTM net income, latest diluted shares,
a positive revenue base) are missing. `consensus_eps` and the Lens/gap fields are populated only
when a consensus forward EPS is present (the Saturn EPS + trailing inputs still render without
consensus).

Wired in `saturn/ingestion/dossier.py` alongside the existing forward/consensus computation, set as
`dossier.driver_model`.

## 5. Confidence / soft-fail

- **Soft-fail to `None`** when a required input is missing or revenue base ≤ 0.
- **`low_confidence = True`** (with a caveat string) when the bridge is unreliable: negative
  trailing net margin, negative or absent trailing growth, or an extreme implied figure (e.g.
  `consensus_implied_growth` beyond a sane bound). This mirrors `is_reverse_dcf_low_confidence`.
  A low-confidence driver model still renders, clearly caveated.

## 6. Rendering + wiring (stance unchanged)

- **Render:** a `### Driver Bridge` subsection **under §2 Alpha Thesis** (a `###`, so no section
  renumbering — it's the quantitative basis of the variant). Shows: Saturn forward EPS with its
  inputs (`rev g%, net margin m%`), the gap vs consensus EPS (abs + %), and both lenses ("consensus
  needs +X% growth OR Y% margin"), plus any `low_confidence` caveat. Absent when `driver_model` is
  `None`.
- **Agent context:** `_company_context` (equity_research) renders the driver model so `analyze`,
  `debate`, and `synthesize` all see it.
- **Synthesist prompt nudge:** `SYNTHESIZE_SYSTEM` (or `_synthesize_prompt`) instructs the variant /
  rationale to **cite the driver gap when a driver model is present** — e.g. "the market's number
  needs growth the trailing trend does not support."
- **Stance derivation is UNTOUCHED** — it stays the price-based v1.1/v1.2 derivation (base-case
  return vs consensus target upside). The driver model supplies the *evidence* for the variant, not
  a new stance axis.

## 7. Testing

- **Bridge math:** fixture (TTM revenue, net income, shares, 3y revenue facts) → exact
  `saturn_eps`, `trailing_revenue_growth`, `trailing_net_margin`.
- **Consensus decomposition:** Lens A growth and Lens B margin computed correctly; `eps_gap` /
  `eps_gap_pct` correct; fields `None` when no consensus.
- **Soft-fail:** missing revenue / shares / net income → `None`; missing consensus → Saturn EPS
  present, consensus/gap fields `None`.
- **Low confidence:** negative trailing margin (or absent growth) → `low_confidence=True` + caveat.
- **Dossier wiring:** `build_dossier` attaches `driver_model` (via an injected/mocked compute).
- **Render:** the `### Driver Bridge` subsection appears with the numbers under §2; absent when
  `driver_model is None`; caveat shown when low-confidence.
- **Context + prompt:** `_company_context` includes the driver model; the synthesist prompt
  references the driver gap.

## 8. Scope

- **Modify/create:** `saturn/analytics/driver.py` (new), `saturn/models.py` (`DriverModel` +
  `CompanyDossier.driver_model`), `saturn/ingestion/dossier.py` (wire compute),
  `saturn/workflows/equity_research.py` (`_company_context` render + synthesist prompt nudge),
  `saturn/reports/markdown_report.py` (§2 Driver Bridge subsection); touched tests.

## 9. Out of scope (later slices)

- Segment/KPI build-up (Slice 2); sector-specific driver libraries (Slice 3).
- Consensus-revenue ingestion → clean 2-factor waterfall.
- FCF bridge, multi-year horizon.
- Any change to the deterministic stance derivation, the scenario table, or the anchor.

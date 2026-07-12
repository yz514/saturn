# Consensus-Revenue Ingestion & Growth-vs-Margin Waterfall — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** Driver Model Slice 1 + Guidance-Anchoring (Slice 1.5), both merged to `main`.

## 1. Goal & honest framing

The driver model currently decomposes the EPS gap vs consensus with a **two-lens** view (Lens A:
the growth consensus needs at trailing margin; Lens B: the margin it needs at trailing growth) —
a workaround for lacking consensus revenue. This slice ingests the analyst **forward revenue**
estimate so the gap becomes a clean **2-factor attribution waterfall**: how much of the gap is our
lower/higher growth vs our lower/higher margin.

**Honest boundary:** yfinance forward *revenue* is not in the reliable `.info` fields — it lives in
the `revenue_estimate` analysis table, which is flaky and periodically returns empty. So the
waterfall appears only when the estimate is **present AND passes consistency validation**;
otherwise the driver model keeps today's two-lens. Validation + fallback are load-bearing, not
optional.

## 2. The waterfall math (deterministic 2-factor attribution)

Given TTM revenue `rev`, Saturn growth `g_s` (trend or guidance), Saturn margin `m_s`, shares `s`,
Saturn EPS `E_s = rev·(1+g_s)·m_s/s`; and consensus revenue `R_c`, consensus EPS `E_c`:

- consensus growth `g_c = R_c/rev − 1`
- consensus margin `m_c = E_c·s / R_c`
- **growth effect** `= rev·(g_c − g_s)·m_s/s`   (revalue at Saturn's margin)
- **margin effect** `= rev·(1+g_c)·(m_c − m_s)/s`  (then revalue at consensus growth)

`E_s + growth_effect + margin_effect = E_c` exactly (verified algebraically), so the two effects
sum to the EPS gap `E_c − E_s`. This is a **sequential** (growth-first) bridge; the growth×margin
cross-term lands in the margin leg — noted, standard, and immaterial at these magnitudes.

Rendered as: *"of consensus's $0.50 EPS premium, +$0.35 is their higher growth (15.4% vs our
12.4%), +$0.15 is their higher margin (40.4% vs our 39.3%)."*

## 3. Ingestion + validation (`saturn/ingestion/consensus.py`)

- **`fetch_consensus`:** best-effort read of the **next-FY (+1y) average** from
  `yf.Ticker(ticker).revenue_estimate` → `RawConsensus.forward_revenue`. Wrapped in the same
  defensive try/except as the existing `earnings_history` read (the analysis table breaks across
  yfinance versions → stays `None`, never raises).
- **`validate_consensus`** (already receives `fundamentals`): a **consistency gate** placed AFTER
  the forward-EPS block (so it uses the already-validated `snap.forward_eps`). Using shares (latest
  `WeightedAverageSharesDiluted`) and TTM revenue (`_ttm_or_fy(idx, "Revenues")`) from
  `fundamentals`:
  - if `snap.forward_eps`, `shares`, `ttm_rev` all available and `forward_revenue > 0`:
    - `m_c = snap.forward_eps · shares / forward_revenue`; `g_c = forward_revenue/ttm_rev − 1`
    - accept onto `snap.forward_revenue` **iff** `0 < m_c < 0.6` AND `−0.5 ≤ g_c ≤ 1.0`
    - else append `f"forward_revenue: rejected — implies margin {m_c:.0%} / growth {g_c:+.0%}"` to
      `rejected` and leave `snap.forward_revenue = None`
  - else append `"forward_revenue: no baseline (shares/revenue/forward_eps) to validate"`.
  - Import `_index`, `_ttm_or_fy`, `_fact`, `_annual_periods` from `saturn.analytics.metrics`
    (module-level; `metrics` does not import `consensus`, so no cycle).

## 4. Data model (`saturn/models.py`)

- `RawConsensus` (dataclass): add `forward_revenue: float | None = None`.
- `ConsensusSnapshot`: add `forward_revenue: float | None = None`.
- `DriverModel`: add the waterfall fields (all default `None`), keeping the existing two-lens
  fields as the fallback:
  ```python
      consensus_revenue: float | None = None
      consensus_growth: float | None = None      # g_c
      consensus_margin: float | None = None       # m_c
      gap_from_growth: float | None = None        # growth effect on the EPS gap
      gap_from_margin: float | None = None        # margin effect on the EPS gap
  ```

## 5. Driver model (`saturn/analytics/driver.py`)

Inside the existing `if consensus_eps:` block, after the two-lens computation, add: when
`consensus is not None and consensus.forward_revenue` (already validated) and `rev_ttm > 0`,
compute `g_c`, `m_c`, `growth_effect`, `margin_effect` per §2 and populate the five new
`DriverModel` waterfall fields. The two-lens fields still compute (cheap); render prefers the
waterfall. When `forward_revenue` is absent → all waterfall fields stay `None` and behavior is
unchanged. Stance/scenarios untouched.

## 6. Render (`saturn/reports/markdown_report.py`)

In `_render_driver_bridge`, when `dm.consensus_revenue is not None` (waterfall available), REPLACE
the two "Consensus implies …" lens lines with a single attribution line:
```
- **Gap attribution:** +$0.35 growth (cons 15.4% vs 12.4%) · +$0.15 margin (cons 40.4% vs 39.3%)
```
(signs and figures from `gap_from_growth`/`gap_from_margin`, `consensus_growth` vs
`trailing_revenue_growth`, `consensus_margin` vs `trailing_net_margin`). When
`consensus_revenue is None` → the existing two-lens lines render unchanged.

## 7. Testing

- **`validate_consensus`:** a plausible `forward_revenue` (consistent margin+growth) → accepted onto
  the snapshot; an inconsistent one (implied margin > 0.6 or growth out of band) → rejected (None +
  a `rejected` entry); no shares/revenue/forward_eps baseline → rejected with the no-baseline reason.
- **`fetch_consensus`:** with `yf` patched to expose a `revenue_estimate`, `raw.forward_revenue` is
  read; when the attribute is missing/raises, it stays `None` (defensive).
- **`compute_driver_model` waterfall:** given consensus_eps + forward_revenue, `gap_from_growth +
  gap_from_margin ≈ consensus_eps − saturn_eps` (algebraic identity to 1e-6); `consensus_growth`/
  `consensus_margin` correct; without `forward_revenue`, all waterfall fields `None` (two-lens
  behavior unchanged).
- **Render:** waterfall present → the "Gap attribution" line with both components; absent → the
  two-lens lines. Mutually exclusive.
- **Live:** regenerate a name where yfinance returns a revenue estimate → confirm the waterfall;
  where it doesn't → confirm the clean two-lens fallback.

## 8. Scope

- **Modify:** `saturn/ingestion/consensus.py` (fetch + validate), `saturn/models.py` (three
  additions), `saturn/analytics/driver.py` (waterfall computation),
  `saturn/reports/markdown_report.py` (attribution line); touched tests.

## 9. Out of scope

- Multi-year revenue ramps; using consensus revenue to inform the scenario table; an FCF-based
  waterfall; any change to the stance derivation or the anchor.
- Non-yfinance revenue-estimate sources.

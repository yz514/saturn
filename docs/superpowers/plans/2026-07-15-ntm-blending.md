# Fiscal-Year-Aware NTM Blending Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blend FY0/FY1 consensus estimates by fiscal-year progress into one NTM figure the whole report speaks in; fix the waterfall attribution sign; tighten the prose base-return tolerance.

**Architecture:** `fetch_consensus` reads both estimate rows + the fiscal-year-end date; `validate_consensus` blends them into the existing `forward_eps_ntm`/`forward_revenue` fields (so the driver inherits the fix unchanged); `_resolve_anchor` derives NTM P/E from that same EPS, making the anchor/driver contradiction unrepresentable.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-ntm-blending-design.md`

**File structure:**
- `saturn/ingestion/consensus.py` — blend helpers (T1), raw fields + fetch (T2), validate/blend/gate (T3)
- `saturn/models.py` — `ConsensusSnapshot.ntm_weight` (T3)
- `saturn/analytics/driver.py` — drop FY+1 fallback, negate waterfall legs (T4)
- `saturn/agents/synthesist.py` — anchor NTM P/E, `_PROSE_RETURN_TOL` (T5)

**One clarification of the spec (deliberate):** spec §2.2 reads as if the NTM EPS and revenue are set together on gate success. This plan sets **`forward_eps_ntm` independently of the revenue gate**; the margin/growth gate guards **only** `forward_revenue`. Rationale: yfinance's `revenue_estimate` is documented-flaky, and pairing would discard a perfectly good NTM EPS (killing the whole consensus gap) whenever revenue estimates are missing. This matches spec §2.2's own fallback sentence ("*that* NTM figure stays None"), which is per-series.

---

### Task 1: blend helpers

**Files:**
- Modify: `saturn/ingestion/consensus.py` (constants near `REVENUE_GROWTH_BAND` ~line 24; helpers above `validate_consensus`)
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/ingestion/test_consensus.py` (it already imports `date` via `from datetime import date`? if not, add it):

```python
from datetime import date as _date
from saturn.ingestion.consensus import _ntm_weight, _blend_ntm


def test_ntm_weight_zero_when_fiscal_year_already_ended():
    # MSFT case: FY0 ended 2026-06-30, today 2026-07-15 -> nothing of FY0 remains
    assert _ntm_weight(_date(2026, 6, 30), _date(2026, 7, 15)) == 0.0


def test_ntm_weight_mid_year():
    # AMZN case: FY0 ends 2026-12-31, today 2026-07-15 -> ~5.6 months left -> ~0.46
    w = _ntm_weight(_date(2026, 12, 31), _date(2026, 7, 15))
    assert 0.45 < w < 0.48


def test_ntm_weight_clamps_to_one_beyond_twelve_months():
    assert _ntm_weight(_date(2028, 1, 1), _date(2026, 7, 15)) == 1.0


def test_ntm_weight_none_without_fiscal_year_end():
    assert _ntm_weight(None, _date(2026, 7, 15)) is None


def test_blend_ntm_endpoints_and_midpoint():
    assert _blend_ntm(0.0, 8.66, 9.88) == 9.88          # FY0 elapsed -> pure FY1
    assert _blend_ntm(1.0, 8.66, 9.88) == 8.66          # FY0 entirely ahead -> pure FY0
    assert abs(_blend_ntm(0.4627, 8.66, 9.88) - 9.32) < 0.01


def test_blend_ntm_none_when_any_input_missing():
    assert _blend_ntm(None, 8.66, 9.88) is None
    assert _blend_ntm(0.5, None, 9.88) is None
    assert _blend_ntm(0.5, 8.66, None) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -k "ntm_weight or blend_ntm" -v`
Expected: FAIL — `ImportError: cannot import name '_ntm_weight'`.

- [ ] **Step 3: Implement**

In `saturn/ingestion/consensus.py`, add a constant next to the other gate constants (after `REVENUE_GROWTH_BAND`):

```python
_DAYS_PER_MONTH = 30.44           # average month length, for fiscal-year-progress weighting
```

Add these pure helpers immediately above `def validate_consensus(`:

```python
def _ntm_weight(fy0_end: date | None, today: date) -> float | None:
    """FY0's share of the next twelve months. The `0y` estimate is a valid NTM proxy only early in a
    fiscal year; late in the FY it collapses toward TTM. None when the fiscal-year end is unknown."""
    if fy0_end is None:
        return None
    months_left = max(0.0, (fy0_end - today).days / _DAYS_PER_MONTH)
    return min(1.0, months_left / 12.0)


def _blend_ntm(w: float | None, v0: float | None, v1: float | None) -> float | None:
    """Fiscal-year-progress-weighted next-twelve-months value: w*FY0 + (1-w)*FY1.
    None unless the weight and BOTH fiscal years are known."""
    if w is None or v0 is None or v1 is None:
        return None
    return w * v0 + (1.0 - w) * v1
```

`date` is already imported at the top of the module (`from datetime import date`).

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -k "ntm_weight or blend_ntm" -v`
Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): fiscal-year-progress NTM blend helpers"
```

---

### Task 2: raw FY0/FY1 fields + fetch both rows and the fiscal-year end

**Files:**
- Modify: `saturn/ingestion/consensus.py` (`RawConsensus`; `fetch_consensus`)
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_fetch_consensus_reads_forward_revenue` and `test_fetch_consensus_forward_revenue_defensive` with:

```python
def test_fetch_consensus_reads_both_years_and_fiscal_year_end(monkeypatch):
    import pandas as pd
    from saturn.ingestion import consensus as C
    rev_df = pd.DataFrame({"avg": [70e9, 84e9]}, index=["0y", "+1y"])
    eps_df = pd.DataFrame({"avg": [5.5, 6.6]}, index=["0y", "+1y"])

    class _T:
        info = {"forwardEps": 6.6, "nextFiscalYearEnd": 1798761600}   # 2026-12-31 UTC
        earnings_history = None
        revenue_estimate = rev_df
        earnings_estimate = eps_df

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    raw = C.fetch_consensus("X")
    assert raw.rev_fy0 == 70e9 and raw.rev_fy1 == 84e9
    assert raw.eps_fy0 == 5.5 and raw.eps_fy1 == 6.6
    assert raw.fy0_end is not None and raw.fy0_end.year == 2026


def test_fetch_consensus_estimate_sources_defensive(monkeypatch):
    from saturn.ingestion import consensus as C

    class _T:
        info = {}
        earnings_history = None

        @property
        def revenue_estimate(self):
            raise RuntimeError("analysis endpoint down")

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    raw = C.fetch_consensus("X")          # must not raise
    assert raw.rev_fy0 is None and raw.rev_fy1 is None
    assert raw.eps_fy0 is None and raw.eps_fy1 is None
    assert raw.fy0_end is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -k fetch_consensus -v`
Expected: FAIL — `AttributeError: 'RawConsensus' object has no attribute 'rev_fy0'`.

- [ ] **Step 3: Implement**

(3a) In `RawConsensus`, **replace** the two derived fields
```python
    forward_revenue: float | None = None
    forward_eps_ntm: float | None = None
```
with the blend inputs (they are raw; the NTM figures are now *derived* in `validate_consensus`):
```python
    eps_fy0: float | None = None
    eps_fy1: float | None = None
    rev_fy0: float | None = None
    rev_fy1: float | None = None
    fy0_end: date | None = None        # end of the CURRENT fiscal year (.info nextFiscalYearEnd)
```

(3b) Change the module's datetime import from `from datetime import date` to:
```python
from datetime import date, datetime
```

(3c) Add a module-level helper above `fetch_consensus`:
```python
def _estimate_avg(frame, period: str) -> float | None:
    """Read one period's `avg` from a yfinance estimate table; None when absent or NaN."""
    if frame is None or "avg" not in getattr(frame, "columns", []) or period not in getattr(frame, "index", []):
        return None
    v = frame.loc[period, "avg"]
    return float(v) if v is not None and float(v) == float(v) else None      # reject NaN
```

(3d) In `fetch_consensus`, **replace** the existing `revenue_estimate` and `earnings_estimate` blocks
(the two `try:` blocks that read only the `"0y"` row) with:

```python
    # Estimates for BOTH fiscal years + the current FY's end date, so validate_consensus can blend them
    # into a true next-twelve-months figure. Each source is independently best-effort.
    try:
        est = handle.revenue_estimate
        raw.rev_fy0, raw.rev_fy1 = _estimate_avg(est, "0y"), _estimate_avg(est, "+1y")
    except Exception as exc:  # noqa: BLE001 - revenue estimate is optional
        logger.debug("consensus revenue_estimate unavailable for %s: %s", ticker, exc)
    try:
        ee = handle.earnings_estimate
        raw.eps_fy0, raw.eps_fy1 = _estimate_avg(ee, "0y"), _estimate_avg(ee, "+1y")
    except Exception as exc:  # noqa: BLE001 - earnings estimate is optional
        logger.debug("consensus earnings_estimate unavailable for %s: %s", ticker, exc)
    try:
        ts = info.get("nextFiscalYearEnd")
        if ts:
            raw.fy0_end = datetime.utcfromtimestamp(int(ts)).date()
    except Exception as exc:  # noqa: BLE001 - fiscal-year end is optional
        logger.debug("consensus fiscal-year end unavailable for %s: %s", ticker, exc)
    return raw
```
(`info` is already a local in `fetch_consensus`: `info = handle.info or {}`.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -k fetch_consensus -v` → PASS.
(Other tests in the file will fail until Task 3 — that is expected.)

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): fetch FY0+FY1 estimates and the fiscal-year end"
```

---

### Task 3: `ntm_weight` field + blend in `validate_consensus`

**Files:**
- Modify: `saturn/models.py` (`ConsensusSnapshot`); `saturn/ingestion/consensus.py` (`validate_consensus`)
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write/revise the tests**

(1a) In `saturn/models.py`'s `ConsensusSnapshot`, the comment on `forward_eps_ntm` is now wrong — it will be updated in Step 3.

(1b) **Rewrite** the four revenue-gate tests. They previously built `RawConsensus(forward_eps_ntm=..., forward_revenue=...)`. Now they supply FY0/FY1 + a fiscal-year end + an injected `today`. Setting **FY0 == FY1** makes the blend equal that value for *any* weight, preserving each test's original intent exactly:

```python
def test_forward_revenue_accepted_when_consistent():
    # FY0 == FY1 so the blend is exactly 5.0 / 100e9 regardless of the weight.
    # NTM EPS 5.0 x 10e9 shares / 100e9 rev = 0.5 margin (ok); 100/90-1 = +11% growth (ok).
    raw = RawConsensus(forward_eps=6.2, eps_fy0=5.0, eps_fy1=5.0,
                       rev_fy0=100e9, rev_fy1=100e9, fy0_end=_date(2026, 12, 31))
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_revenue == 100e9 and c.forward_eps_ntm == 5.0
    assert c.ntm_weight is not None
    assert not any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_rejected_when_implausible():
    # fr 40e9 -> implied margin 50e9/40e9 = 1.25 (>0.6) -> revenue rejected.
    # The NTM EPS survives: the gate guards the REVENUE, not the (independently blended) EPS.
    raw = RawConsensus(forward_eps=6.2, eps_fy0=5.0, eps_fy1=5.0,
                       rev_fy0=40e9, rev_fy1=40e9, fy0_end=_date(2026, 12, 31))
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_revenue is None
    assert c.forward_eps_ntm == 5.0
    assert any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_no_baseline_rejected():
    # no Revenues/shares facts -> cannot validate the revenue
    raw = RawConsensus(forward_eps=6.2, eps_fy0=5.0, eps_fy1=5.0,
                       rev_fy0=100e9, rev_fy1=100e9, fy0_end=_date(2026, 12, 31))
    fund = Fundamentals(facts=[FinancialFact(
        concept="EarningsPerShareDiluted", value=4.5, unit="USD/shares", fiscal_period="FY2024", provenance=PROV)])
    c = validate_consensus(raw, fund, _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_revenue is None
    assert any("no baseline" in r for r in c.rejected)


def test_forward_revenue_needs_ntm_eps_not_anchor_eps():
    # The revenue gate validates against the blended NTM EPS, not the FY+1 anchor EPS. With no FY0/FY1
    # EPS there is no NTM EPS baseline, so the revenue is not admitted.
    raw = RawConsensus(forward_eps=5.0, rev_fy0=100e9, rev_fy1=100e9, fy0_end=_date(2026, 12, 31))
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_eps == 5.0             # anchor EPS still validated for the FY+1 P/E
    assert c.forward_eps_ntm is None
    assert c.forward_revenue is None
    assert any("no baseline" in r for r in c.rejected)
```

(1c) **Add** three new tests:

```python
def test_validate_blends_ntm_by_fiscal_year_progress():
    # AMZN-like: FY0 ends 2026-12-31, today 2026-07-15 -> w ~= 0.46 -> NTM EPS ~= 9.32
    raw = RawConsensus(forward_eps=9.88, eps_fy0=8.66, eps_fy1=9.88, fy0_end=_date(2026, 12, 31))
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert abs(c.forward_eps_ntm - 9.32) < 0.02
    assert 0.45 < c.ntm_weight < 0.48


def test_validate_ntm_is_pure_fy1_when_fiscal_year_already_ended():
    # MSFT-like: FY0 ended 2026-06-30 -> w == 0 -> NTM EPS == FY1 EPS
    raw = RawConsensus(forward_eps=19.36, eps_fy0=16.82, eps_fy1=19.36, fy0_end=_date(2026, 6, 30))
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_eps_ntm == 19.36 and c.ntm_weight == 0.0


def test_validate_skips_ntm_without_fiscal_year_end():
    raw = RawConsensus(forward_eps=6.2, eps_fy0=5.0, eps_fy1=6.0, rev_fy0=100e9, rev_fy1=100e9)
    c = validate_consensus(raw, _rev_fund(), _quote(50.0), today=_date(2026, 7, 15))
    assert c.forward_eps_ntm is None and c.forward_revenue is None and c.ntm_weight is None
    assert any("NTM blend" in r for r in c.rejected)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q`
Expected: FAIL — `validate_consensus() got an unexpected keyword argument 'today'` / missing `ntm_weight`.

- [ ] **Step 3: Implement**

(3a) In `saturn/models.py`, in `ConsensusSnapshot`, replace the `forward_eps_ntm` line and add `ntm_weight`:
```python
    forward_eps_ntm: float | None = None  # blended next-twelve-months EPS (w*FY0 + (1-w)*FY1)
    ntm_weight: float | None = None       # w: FY0's share of the blend (fiscal-year progress)
```
(Leave `forward_revenue` where it is; its meaning is now the blended NTM revenue — update its comment if it has one.)

(3b) In `saturn/ingestion/consensus.py`, change the signature:
```python
def validate_consensus(
    raw: RawConsensus, fundamentals: Fundamentals | None, quote: Quote | None,
    *, today: date | None = None,
) -> ConsensusSnapshot:
```
(`today` is injectable purely so tests are deterministic; production callers omit it.)

(3c) **Replace** the entire existing `# --- forward revenue ... ---` block (from `fr = raw.forward_revenue` through its `rejected.append("forward_revenue: no baseline ...")`) with:

```python
    # --- NTM consensus: blend FY0/FY1 by fiscal-year progress ---
    # The `0y` row alone is only an NTM proxy early in a fiscal year; late in the FY it collapses toward
    # TTM (understating consensus). The EPS blend stands on its own; the gate below guards only revenue.
    w = _ntm_weight(raw.fy0_end, today or date.today())
    ntm_eps = _blend_ntm(w, raw.eps_fy0, raw.eps_fy1)
    ntm_rev = _blend_ntm(w, raw.rev_fy0, raw.rev_fy1)
    if ntm_eps is None and ntm_rev is None:
        rejected.append("NTM blend: unavailable (no fiscal-year-end or incomplete FY0/FY1 estimates)")
    if ntm_eps is not None:
        snap.forward_eps_ntm = ntm_eps
        snap.ntm_weight = w

    # --- forward revenue (consistency gate on the BLENDED figures: implied margin & growth must be sane) ---
    if ntm_rev is not None:
        idx = _index(fundamentals)
        annual = _annual_periods(idx)
        ttm = _ttm_or_fy(idx, "Revenues")
        shares_fact = _fact(idx, "WeightedAverageSharesDiluted", annual[0]) if annual else None
        if ntm_eps and ntm_eps > 0 and ttm and ttm[0] > 0 and shares_fact and shares_fact.value > 0 and ntm_rev > 0:
            m_c = ntm_eps * shares_fact.value / ntm_rev
            g_c = ntm_rev / ttm[0] - 1
            lo, hi = REVENUE_GROWTH_BAND
            if 0 < m_c < REVENUE_MARGIN_CAP and lo <= g_c <= hi:
                snap.forward_revenue = ntm_rev
            else:
                rejected.append(f"forward_revenue: rejected — implies margin {m_c:.0%} / growth {g_c:+.0%}")
        else:
            rejected.append("forward_revenue: no baseline (shares/revenue/NTM EPS) to validate")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q` → whole file green.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): blend FY0/FY1 into a true NTM EPS/revenue by fiscal-year progress"
```

---

### Task 4: driver — drop the FY+1 fallback, negate the waterfall legs

**Files:**
- Modify: `saturn/analytics/driver.py`
- Test: `tests/analytics/test_driver.py`

- [ ] **Step 1: Write/revise the tests**

Add to `tests/analytics/test_driver.py`:

```python
def test_driver_no_consensus_comparison_without_ntm_eps():
    # The FY+1 fallback is deliberately gone: without a horizon-correct NTM EPS we make NO comparison
    # rather than silently comparing against a figure up to two years forward.
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps=4.0, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.consensus_eps is None and dm.eps_gap is None
    assert dm.gap_from_growth is None and dm.consensus_implied_growth is None


def test_waterfall_legs_sum_to_eps_gap_saturn_below_consensus():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps_ntm=2.5, forward_revenue=1100.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.eps_gap < 0                                            # Saturn below consensus
    assert abs((dm.gap_from_growth + dm.gap_from_margin) - dm.eps_gap) < 1e-6
    assert dm.gap_from_growth < 0                                    # signs follow the gap direction


def test_waterfall_legs_sum_to_eps_gap_saturn_above_consensus():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps_ntm=0.5, forward_revenue=900.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.eps_gap > 0                                            # Saturn above consensus
    assert abs((dm.gap_from_growth + dm.gap_from_margin) - dm.eps_gap) < 1e-6
```

Also **update** the existing `test_driver_waterfall_identity_and_values`: its identity assertion
`abs((dm.gap_from_growth + dm.gap_from_margin) - (2.5 - dm.saturn_eps)) < 1e-6` asserts the OLD
(`consensus − saturn`) convention. Change that line to the new convention:
```python
    assert abs((dm.gap_from_growth + dm.gap_from_margin) - (dm.saturn_eps - 2.5)) < 1e-6
```
and change its `cons` construction from `forward_eps=2.5` to `forward_eps_ntm=2.5` (the fallback is gone).
Do the same `forward_eps=` → `forward_eps_ntm=` substitution in the other driver tests that rely on a
consensus comparison (`test_driver_consensus_decomposition_two_lenses`, the extreme-growth test,
`test_driver_no_waterfall_without_forward_revenue`, `test_driver_prefers_ntm_eps_over_forward_eps`).
For `test_driver_prefers_ntm_eps_over_forward_eps`, its premise (NTM preferred *over* a fallback) is now
absorbed by `test_driver_no_consensus_comparison_without_ntm_eps` — **delete that test**.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q`
Expected: FAIL — the new no-fallback test sees `consensus_eps == 4.0`; the sign tests fail the identity.

- [ ] **Step 3: Implement**

(3a) Replace the consensus-EPS selection:
```python
    consensus_eps = None
    if consensus is not None:
        consensus_eps = consensus.forward_eps_ntm or consensus.forward_eps
```
with:
```python
    # Only the horizon-correct blended NTM EPS is comparable to Saturn's 1-year-forward bridge. There is
    # deliberately NO fallback to the FY+1 `forward_eps`: that is up to two years forward and silently
    # reintroduces the horizon bug. No NTM EPS => no consensus comparison.
    consensus_eps = consensus.forward_eps_ntm if consensus is not None else None
```

(3b) Negate both waterfall legs so they sum to the displayed gap. Replace:
```python
            gap_from_growth = rev_ttm * (consensus_growth - g) * margin / shares
            gap_from_margin = rev_ttm * (1 + consensus_growth) * (consensus_margin - margin) / shares
```
with:
```python
            # Signed to match the displayed gap: gap_from_growth + gap_from_margin == eps_gap
            # (= saturn_eps - consensus_eps), so "+0.71 above consensus: +0.08 growth, +0.63 margin".
            gap_from_growth = rev_ttm * (g - consensus_growth) * margin / shares
            gap_from_margin = rev_ttm * (1 + consensus_growth) * (margin - consensus_margin) / shares
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q` → green.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/driver.py tests/analytics/test_driver.py
git commit -m "fix(driver): require horizon-correct NTM EPS; sign waterfall legs to match the gap"
```

---

### Task 5: anchor speaks NTM P/E; tighten the prose tolerance

**Files:**
- Modify: `saturn/agents/synthesist.py` (`_resolve_anchor`; `_PROSE_RETURN_TOL`)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py`:

```python
def test_anchor_uses_ntm_pe_derived_from_the_same_ntm_eps():
    # AMZN-like: price 254.96 / NTM EPS 9.32 = 27.4x. The anchor and the driver bridge now speak the
    # SAME EPS, so "27.4x" and "$9.32" reconcile by construction.
    from saturn.models import Quote
    cons = ConsensusSnapshot(forward_eps=9.88, forward_pe=25.79, forward_eps_ntm=9.32, ntm_weight=0.46,
                             provenance=Provenance(source="yfinance (estimate)"))
    d = _dossier(consensus=cons, quote=Quote(price=254.96, provenance=Provenance(source="yfinance")))
    a = _resolve_anchor(d)
    assert a.metric == "NTM P/E" and abs(a.value - 27.4) < 0.1 and a.unit == "x"
    assert "NTM P/E 27.4x" in a.text and "$9.32" in a.text
    assert "46% current FY / 54% next FY" in a.text
    assert "FY+1 P/E 25.8x" in a.text and "$9.88" in a.text      # conventional reference retained


def test_anchor_falls_back_to_forward_pe_without_ntm_eps():
    cons = ConsensusSnapshot(forward_pe=6.5, target_mean=180.0, rating="buy", n_analysts=30,
                             provenance=Provenance(source="yfinance (estimate)"))
    a = _resolve_anchor(_dossier(consensus=cons))
    assert a.metric == "Forward P/E" and a.value == 6.5


def test_prose_tolerance_is_rounding_only():
    from saturn.agents.synthesist import _PROSE_RETURN_TOL
    assert _PROSE_RETURN_TOL == 0.02


def test_align_corrects_ten_point_divergence():
    # AMZN case: prose said +12% while the table computed +22% -> 10pp slipped the old 15pp tolerance
    t = _align_thesis(rationale="Base case implies ~+12% vs the Street's +23%.", base_ret=0.22)
    align_prose_base_return(t)
    assert "+22%" in t.rationale and "+12%" not in t.rationale
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "anchor_uses_ntm or tolerance_is_rounding or ten_point" -v`
Expected: FAIL — anchor metric is `"Forward P/E"`; `_PROSE_RETURN_TOL == 0.15`.

- [ ] **Step 3: Implement**

(3a) Change the constant:
```python
_PROSE_RETURN_TOL = 0.02         # prose base return may differ from the computed base return only by rounding
```

(3b) In `_resolve_anchor`, replace the consensus branch's metric selection and the first two `parts`
appends. The branch condition also gains `cons.forward_eps_ntm`:

```python
    cons = dossier.consensus
    if cons is not None and any(v is not None for v in
                                (cons.forward_pe, cons.forward_eps, cons.target_mean, cons.forward_eps_ntm)):
        px = dossier.quote.price if dossier.quote else None
        ntm_pe = (px / cons.forward_eps_ntm
                  if (px and px > 0 and cons.forward_eps_ntm and cons.forward_eps_ntm > 0) else None)
        if ntm_pe is not None:
            metric, value, unit = "NTM P/E", ntm_pe, "x"
        elif cons.forward_pe is not None:
            metric, value, unit = "Forward P/E", cons.forward_pe, "x"
        elif cons.forward_eps is not None:
            metric, value, unit = "forward EPS", cons.forward_eps, "USD/share"
        else:
            metric, value, unit = "mean price target", cons.target_mean, "USD/share"
        parts: list[str] = []
        if ntm_pe is not None:
            blend = (f"; {cons.ntm_weight:.0%} current FY / {1 - cons.ntm_weight:.0%} next FY"
                     if cons.ntm_weight is not None else "")
            parts.append(f"NTM P/E {ntm_pe:.1f}x (on blended NTM EPS ${cons.forward_eps_ntm:.2f}{blend})")
        if cons.forward_pe is not None:
            ref = f" on FY+1 EPS ${cons.forward_eps:.2f}" if cons.forward_eps is not None else ""
            parts.append(f"FY+1 P/E {cons.forward_pe:.1f}x{ref}")
        elif cons.forward_eps is not None and ntm_pe is None:
            parts.append(f"forward EPS ${cons.forward_eps:.2f}/share")
```
Leave the remaining `parts` appends (target_mean, rating, n_analysts), the `text` join, and the
`ExpectationAnchor(...)` return exactly as they are — `period="NTM"` is now accurate when `ntm_pe` is used.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` → whole file green.

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all green. If `tests/test_markdown_report.py` or a workflow test asserts the old
`"forward P/E"` anchor text or a `forward_eps`-based consensus comparison, update those assertions to
the new anchor text / `forward_eps_ntm` field — these are mechanical consequences, not behavioural
weakenings. Report any test whose *intent* would have to change.

- [ ] **Step 6: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): anchor speaks NTM P/E; prose tolerance is rounding-only"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest -q`.
- [ ] Offline sanity (no LLM): build the MSFT and AMZN dossiers and print the blend:
  ```
  .venv/Scripts/python.exe -c "
  from saturn.ingestion.dossier import build_dossier
  for t in ('MSFT','AMZN'):
      d=build_dossier(t, mock=False); c=d.consensus; dm=d.driver_model
      print(t, 'w=%.2f'%c.ntm_weight, 'NTM EPS=%.2f'%c.forward_eps_ntm,
            'NTM P/E=%.1f'%(d.quote.price/c.forward_eps_ntm), 'fwd P/E=%.1f'%c.forward_pe,
            'saturn=%.2f'%dm.saturn_eps, 'gap=%.2f'%dm.eps_gap)"
  ```
  Expect **MSFT**: `w=0.00`, NTM EPS ≈ `19.36`, NTM P/E ≈ `20.4` == fwd P/E (no contradiction),
  gap ≈ **−0.50** (was +2.04). Expect **AMZN**: `w≈0.46`, NTM EPS ≈ `9.32`, gap ≈ **+0.05** (was +0.71).
- [ ] Live (optional; ~10 min each — build the dossier once and pass it to `run()` to dodge yfinance
  flakiness): regenerate MSFT and AMZN; confirm the §2 anchor reads `NTM P/E … (on blended NTM EPS …;
  x% current FY / y% next FY), FY+1 P/E …`, that the Driver Bridge's gap and attribution legs share the
  same sign, and that no `prose_vs_computed` contradiction survives.

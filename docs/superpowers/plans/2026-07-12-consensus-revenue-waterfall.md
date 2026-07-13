# Consensus-Revenue Ingestion & Growth-vs-Margin Waterfall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the analyst forward-revenue estimate (validated for consistency) so the driver model's EPS gap vs consensus is decomposed into a clean growth-vs-margin attribution waterfall, falling back to the existing two-lens when the estimate is absent or fails validation.

**Architecture:** `fetch_consensus` best-effort reads yfinance's `revenue_estimate`; `validate_consensus` accepts it only if the implied margin/growth are sane; `compute_driver_model` computes a deterministic 2-factor bridge (`growth_effect + margin_effect = EPS gap`); the Driver Bridge renders one attribution line when available. Stance/scenarios untouched.

**Tech Stack:** Python 3.13, Pydantic v2, pandas (yfinance), pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-consensus-revenue-waterfall-design.md`.

**File map:**
- `saturn/models.py` — `ConsensusSnapshot.forward_revenue` + 5 `DriverModel` waterfall fields (Task 1)
- `saturn/ingestion/consensus.py` — `RawConsensus.forward_revenue` + fetch (Task 2); validation gate (Task 3)
- `saturn/analytics/driver.py` — waterfall computation (Task 4)
- `saturn/reports/markdown_report.py` — attribution line (Task 5)

---

### Task 1: Model fields

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models_driver.py`

- [ ] **Step 1: Write the failing test** — APPEND to `tests/test_models_driver.py`:

```python
def test_consensus_snapshot_has_forward_revenue():
    from saturn.models import ConsensusSnapshot, Provenance
    c = ConsensusSnapshot(provenance=Provenance(source="yfinance (estimate)"))
    assert c.forward_revenue is None


def test_driver_model_waterfall_fields_default_none():
    from saturn.models import DriverModel, Provenance
    dm = DriverModel(saturn_eps=2.0, trailing_revenue_growth=0.1, trailing_net_margin=0.1, shares=50.0,
                     provenance=Provenance(source="Saturn (model)"))
    assert dm.consensus_revenue is None and dm.consensus_growth is None and dm.consensus_margin is None
    assert dm.gap_from_growth is None and dm.gap_from_margin is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q -k "forward_revenue or waterfall"`
Expected: FAIL (attributes missing).

- [ ] **Step 3: Implement** — in `saturn/models.py`:

In `class ConsensusSnapshot`, add after the `forward_eps` field:
```python
    forward_revenue: float | None = None
```

In `class DriverModel`, add after the existing `consensus_implied_margin` field (keep the two-lens fields):
```python
    consensus_revenue: float | None = None
    consensus_growth: float | None = None       # g_c = forward_revenue/TTM_rev - 1
    consensus_margin: float | None = None        # m_c = consensus_eps * shares / forward_revenue
    gap_from_growth: float | None = None         # EPS-gap growth effect
    gap_from_margin: float | None = None         # EPS-gap margin effect
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_driver.py
git commit -m "feat(models): ConsensusSnapshot.forward_revenue + DriverModel waterfall fields"
```
Commit trailer (all commits): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: `fetch_consensus` reads the forward-revenue estimate

**Files:**
- Modify: `saturn/ingestion/consensus.py`
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/ingestion/test_consensus.py`:

```python
def test_fetch_consensus_reads_forward_revenue(monkeypatch):
    import pandas as pd
    from saturn.ingestion import consensus as C
    df = pd.DataFrame({"avg": [70e9]}, index=["+1y"])

    class _T:
        info = {"forwardEps": 5.0}
        earnings_history = None
        revenue_estimate = df

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    raw = C.fetch_consensus("X")
    assert raw.forward_revenue == 70e9


def test_fetch_consensus_forward_revenue_defensive(monkeypatch):
    from saturn.ingestion import consensus as C

    class _T:
        info = {}
        earnings_history = None

        @property
        def revenue_estimate(self):
            raise RuntimeError("analysis endpoint down")

    monkeypatch.setattr(C, "yf", type("YF", (), {"Ticker": staticmethod(lambda t: _T())}))
    assert C.fetch_consensus("X").forward_revenue is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q -k "forward_revenue"`
Expected: FAIL (`RawConsensus` has no `forward_revenue`).

- [ ] **Step 3: Implement** — in `saturn/ingestion/consensus.py`:

In the `RawConsensus` dataclass, add a field (after `last_estimate_eps`):
```python
    forward_revenue: float | None = None
```

In `fetch_consensus`, after the existing `earnings_history` try/except block and before `return raw`, add:
```python
    # forward revenue estimate (best-effort; the analysis table is flaky across yfinance versions)
    try:
        est = handle.revenue_estimate
        if est is not None and "avg" in getattr(est, "columns", []) and "+1y" in getattr(est, "index", []):
            v = est.loc["+1y", "avg"]
            if v is not None and float(v) == float(v):   # reject NaN
                raw.forward_revenue = float(v)
    except Exception as exc:  # noqa: BLE001 - revenue estimate is optional
        logger.debug("consensus revenue_estimate unavailable for %s: %s", ticker, exc)
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): fetch the forward-revenue estimate (best-effort)"
```

---

### Task 3: `validate_consensus` consistency gate for forward revenue

**Files:**
- Modify: `saturn/ingestion/consensus.py`
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/ingestion/test_consensus.py`:

```python
def _rev_fund(eps=4.5, rev=90e9, shares=10e9):
    return Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=eps, unit="USD/shares", fiscal_period="FY2024", provenance=PROV),
        FinancialFact(concept="Revenues", value=rev, unit="USD", fiscal_period="FY2024", provenance=PROV),
        FinancialFact(concept="WeightedAverageSharesDiluted", value=shares, unit="shares", fiscal_period="FY2024", provenance=PROV)])


def test_forward_revenue_accepted_when_consistent():
    # forward_eps 5.0 x 10e9 shares / 100e9 rev = 0.5 margin (ok); 100/90-1 = +11% growth (ok)
    raw = RawConsensus(forward_eps=5.0, forward_revenue=100e9)
    c = validate_consensus(raw, _rev_fund(), _quote(50.0))
    assert c.forward_eps == 5.0 and c.forward_revenue == 100e9
    assert not any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_rejected_when_implausible():
    # fr 40e9 -> implied margin 50e9/40e9 = 1.25 (>0.6) -> rejected
    raw = RawConsensus(forward_eps=5.0, forward_revenue=40e9)
    c = validate_consensus(raw, _rev_fund(), _quote(50.0))
    assert c.forward_revenue is None
    assert any("forward_revenue" in r for r in c.rejected)


def test_forward_revenue_no_baseline_rejected():
    # no Revenues/shares facts -> cannot validate
    raw = RawConsensus(forward_eps=5.0, forward_revenue=100e9)
    fund = Fundamentals(facts=[FinancialFact(
        concept="EarningsPerShareDiluted", value=4.5, unit="USD/shares", fiscal_period="FY2024", provenance=PROV)])
    c = validate_consensus(raw, fund, _quote(50.0))
    assert c.forward_revenue is None
    assert any("no baseline" in r for r in c.rejected)
```
(`RawConsensus`, `validate_consensus`, `Fundamentals`, `FinancialFact`, `PROV`, `_quote` already exist/are imported in this test file.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q -k "forward_revenue_accepted or forward_revenue_rejected or no_baseline"`
Expected: FAIL (`c.forward_revenue` is always None; no validation logic).

- [ ] **Step 3: Implement** — in `saturn/ingestion/consensus.py`:

Add a module-level import near the top (with the other imports):
```python
from saturn.analytics.metrics import _annual_periods, _fact, _index, _ttm_or_fy
```

Add two named constants near the existing `EPS_GROWTH_BAND` / `PE_CONSISTENCY_TOL`:
```python
REVENUE_MARGIN_CAP = 0.6          # implied consensus net margin must be below this
REVENUE_GROWTH_BAND = (-0.5, 1.0)  # implied consensus revenue growth must be within this
```

In `validate_consensus`, immediately AFTER the forward-EPS block (the `# --- forward EPS / forward PE / PEG ---` block that ends by setting `snap.forward_eps`) and BEFORE the `# --- price targets ---` block, insert:
```python
    # --- forward revenue (consistency gate: implied margin & growth must be sane) ---
    fr = raw.forward_revenue
    if fr is not None:
        idx = _index(fundamentals)
        annual = _annual_periods(idx)
        ttm = _ttm_or_fy(idx, "Revenues")
        shares_fact = _fact(idx, "WeightedAverageSharesDiluted", annual[0]) if annual else None
        if snap.forward_eps and ttm and ttm[0] > 0 and shares_fact and shares_fact.value > 0 and fr > 0:
            m_c = snap.forward_eps * shares_fact.value / fr
            g_c = fr / ttm[0] - 1
            lo, hi = REVENUE_GROWTH_BAND
            if 0 < m_c < REVENUE_MARGIN_CAP and lo <= g_c <= hi:
                snap.forward_revenue = fr
            else:
                rejected.append(f"forward_revenue: rejected — implies margin {m_c:.0%} / growth {g_c:+.0%}")
        else:
            rejected.append("forward_revenue: no baseline (shares/revenue/forward_eps) to validate")
```

Note: the `rejected` list is already attached to the returned snapshot at the end of `validate_consensus` (the function assigns `snap.rejected = rejected` before returning — verify this line exists; if the snapshot is constructed with `rejected=rejected`, no change needed).

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): validate forward revenue for internal consistency (margin/growth bands)"
```

---

### Task 4: `compute_driver_model` growth-vs-margin waterfall

**Files:**
- Modify: `saturn/analytics/driver.py`
- Test: `tests/analytics/test_driver.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/analytics/test_driver.py`:

```python
def test_driver_waterfall_identity_and_values():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps=2.5, forward_revenue=1100.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    # consensus growth = 1100/1000 - 1 = 0.10; consensus margin = 2.5*50/1100
    assert abs(dm.consensus_growth - 0.10) < 1e-9
    assert abs(dm.consensus_margin - (2.5 * 50 / 1100)) < 1e-9
    # 2-factor identity: growth effect + margin effect == consensus_eps - saturn_eps
    assert abs((dm.gap_from_growth + dm.gap_from_margin) - (2.5 - dm.saturn_eps)) < 1e-6
    assert dm.consensus_revenue == 1100.0


def test_driver_no_waterfall_without_forward_revenue():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps=2.5, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.consensus_revenue is None and dm.gap_from_growth is None and dm.gap_from_margin is None
    assert dm.consensus_implied_growth is not None   # two-lens still present
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q -k "waterfall"`
Expected: FAIL (`gap_from_growth` is None / attribute unset).

- [ ] **Step 3: Implement** — in `saturn/analytics/driver.py`, in `compute_driver_model`.

Where the consensus locals are initialised (the line `eps_gap = eps_gap_pct = implied_g = implied_m = None`), add the waterfall locals:
```python
    eps_gap = eps_gap_pct = implied_g = implied_m = None
    consensus_revenue = consensus_growth = consensus_margin = None
    gap_from_growth = gap_from_margin = None
```

Inside the existing `if consensus_eps:` block, AFTER the two-lens computation (after the `implied_m = ...` / extreme-growth caveat lines), add:
```python
        fr = consensus.forward_revenue if consensus is not None else None
        if fr and rev_ttm > 0:
            consensus_revenue = fr
            consensus_growth = fr / rev_ttm - 1
            consensus_margin = consensus_eps * shares / fr
            gap_from_growth = rev_ttm * (consensus_growth - g) * margin / shares
            gap_from_margin = rev_ttm * (1 + consensus_growth) * (consensus_margin - margin) / shares
```

In the `return DriverModel(...)` call, add the five kwargs (e.g. after `consensus_implied_margin=implied_m,`):
```python
        consensus_revenue=consensus_revenue,
        consensus_growth=consensus_growth,
        consensus_margin=consensus_margin,
        gap_from_growth=gap_from_growth,
        gap_from_margin=gap_from_margin,
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/driver.py tests/analytics/test_driver.py
git commit -m "feat(driver): growth-vs-margin waterfall when consensus revenue is available"
```

---

### Task 5: Render the attribution line (replacing two-lens when available)

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/test_markdown_report.py`:

```python
def _driver_model_waterfall():
    from saturn.models import DriverModel, Provenance
    return DriverModel(saturn_eps=18.86, trailing_revenue_growth=0.124, trailing_net_margin=0.393,
                       shares=7.4e9, consensus_eps=19.36, eps_gap=-0.50, eps_gap_pct=-0.026,
                       consensus_implied_growth=0.154, consensus_implied_margin=0.404,
                       consensus_revenue=290e9, consensus_growth=0.154, consensus_margin=0.404,
                       gap_from_growth=0.35, gap_from_margin=0.15,
                       provenance=Provenance(source="Saturn (model)"))


def test_render_driver_bridge_waterfall_attribution():
    report = _sample_report()
    report.company.driver_model = _driver_model_waterfall()
    md = render(report)
    assert "Gap attribution" in md
    assert "+0.35 EPS from growth" in md
    assert "cons +15.4% vs +12.4%" in md
    assert "Consensus implies" not in md          # two-lens lines are replaced


def test_render_driver_bridge_two_lens_when_no_waterfall():
    report = _sample_report()
    report.company.driver_model = _driver_model()   # no consensus_revenue -> two-lens
    md = render(report)
    assert "Consensus implies" in md
    assert "Gap attribution" not in md
```
(`_driver_model()` already exists in this file and has `consensus_revenue=None`.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q -k "waterfall_attribution or two_lens_when"`
Expected: FAIL (no "Gap attribution" line).

- [ ] **Step 3: Implement** — in `saturn/reports/markdown_report.py`, in `_render_driver_bridge`.

Find the consensus block:
```python
    if dm.consensus_eps is not None:
        gap = f"${dm.eps_gap:+,.2f}" if dm.eps_gap is not None else "N/A"
        pct = f" ({dm.eps_gap_pct:+.0%})" if dm.eps_gap_pct is not None else ""
        out.append(f"- **vs consensus EPS ${dm.consensus_eps:,.2f}:** gap {gap}{pct}")
        if dm.consensus_implied_growth is not None:
            out.append(f"- Consensus implies **rev growth {dm.consensus_implied_growth:+.1%}** (at trailing margin)")
        if dm.consensus_implied_margin is not None:
            out.append(f"- …or **net margin {dm.consensus_implied_margin:.1%}** (at trailing growth)")
```
Replace the two `if dm.consensus_implied_*` lines with a waterfall-preferring branch:
```python
    if dm.consensus_eps is not None:
        gap = f"${dm.eps_gap:+,.2f}" if dm.eps_gap is not None else "N/A"
        pct = f" ({dm.eps_gap_pct:+.0%})" if dm.eps_gap_pct is not None else ""
        out.append(f"- **vs consensus EPS ${dm.consensus_eps:,.2f}:** gap {gap}{pct}")
        if dm.consensus_revenue is not None:
            out.append(
                f"- **Gap attribution:** {dm.gap_from_growth:+.2f} EPS from growth "
                f"(cons {dm.consensus_growth:+.1%} vs {dm.trailing_revenue_growth:+.1%}) · "
                f"{dm.gap_from_margin:+.2f} from margin "
                f"(cons {dm.consensus_margin:.1%} vs {dm.trailing_net_margin:.1%})")
        else:
            if dm.consensus_implied_growth is not None:
                out.append(f"- Consensus implies **rev growth {dm.consensus_implied_growth:+.1%}** (at trailing margin)")
            if dm.consensus_implied_margin is not None:
                out.append(f"- …or **net margin {dm.consensus_implied_margin:.1%}** (at trailing growth)")
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): Driver Bridge growth-vs-margin attribution line (two-lens fallback)"
```

---

## Final verification (live)

Regenerate a report and confirm the waterfall or the fallback (yfinance revenue estimates are intermittent):

```bash
.venv/Scripts/python.exe -m saturn.cli research MSFT
```
In `reports/MSFT_<date>.md`, the `### Driver Bridge`:
1. If yfinance returned a consistent revenue estimate → a single **Gap attribution** line splitting the EPS gap into growth vs margin.
2. Otherwise → the existing two-lens "Consensus implies …" lines (validation/absence fallback).
3. The stance line and scenario table are unchanged either way.

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §3 fetch → Task 2; §3 validation gate → Task 3; §4 model fields → Task 1; §2/§5 waterfall math → Task 4; §6 render → Task 5. §7 tests distributed.
- **Type consistency:** `RawConsensus.forward_revenue` / `ConsensusSnapshot.forward_revenue`; `DriverModel.{consensus_revenue,consensus_growth,consensus_margin,gap_from_growth,gap_from_margin}`; `REVENUE_MARGIN_CAP`/`REVENUE_GROWTH_BAND`. Used identically across tasks. The waterfall identity (`gap_from_growth + gap_from_margin == consensus_eps − saturn_eps`) is asserted in Task 4.
- **Verify before relying:** Task 3 notes to confirm `validate_consensus` already attaches `rejected` to the returned snapshot (it does — the constructor/assignment predates this change); no new plumbing needed for the rejection reasons.
- **No placeholders:** every step has complete code.

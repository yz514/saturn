# Driver Model (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saturn computes its own deterministic trailing-trend forward EPS (revenue→margin→EPS), decomposes the gap vs consensus by driver (two lenses), and surfaces it as evidence for the alpha variant — without changing the stance.

**Architecture:** A pure `saturn/analytics/driver.py` (mirrors `forward.py`) produces a structured `DriverModel` from as-reported data + consensus forward EPS. It attaches to the dossier, renders as a `### Driver Bridge` subsection under §2, and feeds the agent context; the synthesist is nudged to cite the gap. Stance derivation is untouched.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-driver-model-design.md`.

**File map:**
- `saturn/models.py` — `DriverModel` + `CompanyDossier.driver_model` (Task 1)
- `saturn/analytics/driver.py` (NEW) — `compute_driver_model` (Task 2)
- `saturn/ingestion/dossier.py` — wire compute into the mock + real builders (Task 3)
- `saturn/workflows/equity_research.py` — `_company_context` render + synthesist prompt nudge (Task 4)
- `saturn/reports/markdown_report.py` — `### Driver Bridge` subsection (Task 5)

---

### Task 1: `DriverModel` model + dossier field

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models_driver.py` (NEW)

- [ ] **Step 1: Write the failing test** — create `tests/test_models_driver.py`:

```python
from saturn.models import DriverModel, CompanyDossier, Provenance
from datetime import date


def test_driver_model_defaults():
    dm = DriverModel(saturn_eps=2.15, trailing_revenue_growth=0.077, trailing_net_margin=0.10,
                     shares=50.0, provenance=Provenance(source="Saturn (model)"))
    assert dm.horizon == "NTM"
    assert dm.consensus_eps is None and dm.eps_gap is None
    assert dm.low_confidence is False and dm.caveats == []


def test_dossier_has_driver_model_field():
    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 7, 12))
    assert d.driver_model is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q`
Expected: FAIL (`cannot import name 'DriverModel'`).

- [ ] **Step 3: Implement** — in `saturn/models.py`, add this class right after the `AlphaThesis` class + `ALPHA_PROSE_FIELDS` constant (before `CompanyDossier`):

```python
class DriverModel(BaseModel):
    """A deterministic trailing-trend forward-EPS bridge + consensus decomposition. The base
    case is a MECHANICAL trailing-trend baseline (backward-looking), not a forward judgment."""
    horizon: str = "NTM"
    saturn_eps: float                                    # Saturn's trailing-trend forward EPS
    trailing_revenue_growth: float                       # g (3y revenue CAGR)
    trailing_net_margin: float                           # m (TTM net income / TTM revenue)
    shares: float
    consensus_eps: float | None = None
    eps_gap: float | None = None                         # saturn_eps - consensus_eps
    eps_gap_pct: float | None = None
    consensus_implied_growth: float | None = None        # Lens A (hold margin)
    consensus_implied_margin: float | None = None        # Lens B (hold growth)
    low_confidence: bool = False
    caveats: list[str] = Field(default_factory=list)
    provenance: Provenance
```

In `class CompanyDossier`, add the field after `industry_context`:

```python
    driver_model: DriverModel | None = None
```

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_driver.py
git commit -m "feat(models): DriverModel + CompanyDossier.driver_model"
```
Commit trailer (all commits): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: `compute_driver_model` (the bridge + decomposition)

**Files:**
- Create: `saturn/analytics/driver.py`
- Test: `tests/analytics/test_driver.py` (NEW)

- [ ] **Step 1: Write the failing tests** — create `tests/analytics/test_driver.py`:

```python
from saturn.analytics.driver import compute_driver_model
from saturn.models import ConsensusSnapshot, FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _facts(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=PROV)
        for (c, p, v) in rows
    ])


def _base_rows():
    # FY2025 revenue 1000 (TTM falls back to FY), FY2022 800 -> 3y CAGR = (1000/800)^(1/3)-1 ~ 0.0772
    return [
        ("Revenues", "FY2025", 1000.0), ("Revenues", "FY2022", 800.0),
        ("NetIncomeLoss", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2025", 50.0),
    ]


def _quote():
    return Quote(price=100.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))


def test_driver_bridge_math_no_consensus():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None)
    assert dm is not None
    assert abs(dm.trailing_net_margin - 0.10) < 1e-9
    assert abs(dm.trailing_revenue_growth - ((1000 / 800) ** (1 / 3) - 1)) < 1e-9
    # saturn_eps = 1000 * (1+g) * 0.1 / 50
    exp = 1000 * (1 + dm.trailing_revenue_growth) * 0.10 / 50
    assert abs(dm.saturn_eps - exp) < 1e-9
    assert dm.consensus_eps is None and dm.eps_gap is None
    assert dm.low_confidence is False


def test_driver_consensus_decomposition_two_lenses():
    cons = ConsensusSnapshot(forward_eps=2.5, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.consensus_eps == 2.5
    assert abs(dm.eps_gap - (dm.saturn_eps - 2.5)) < 1e-9
    # Lens A: implied growth = (2.5 * 50 / 0.1) / 1000 - 1 = 0.25
    assert abs(dm.consensus_implied_growth - 0.25) < 1e-9
    # Lens B: implied margin = 2.5 * 50 / (1000 * (1+g))
    exp_m = 2.5 * 50 / (1000 * (1 + dm.trailing_revenue_growth))
    assert abs(dm.consensus_implied_margin - exp_m) < 1e-9


def test_driver_soft_fails_without_shares():
    rows = [("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", 100.0)]  # no shares
    assert compute_driver_model(_facts(rows), _quote(), None) is None


def test_driver_low_confidence_on_negative_margin():
    rows = [("Revenues", "FY2025", 1000.0), ("Revenues", "FY2022", 800.0),
            ("NetIncomeLoss", "FY2025", -50.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None)
    assert dm is not None and dm.low_confidence is True
    assert any("margin" in c for c in dm.caveats)


def test_driver_low_confidence_without_growth_history():
    rows = [("Revenues", "FY2025", 1000.0),  # no FY2022 -> no 3y CAGR
            ("NetIncomeLoss", "FY2025", 100.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None)
    assert dm is not None and dm.trailing_revenue_growth == 0.0 and dm.low_confidence is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q`
Expected: FAIL (`ModuleNotFoundError: saturn.analytics.driver`).

- [ ] **Step 3: Implement** — create `saturn/analytics/driver.py`:

```python
"""Driver model: a deterministic trailing-trend forward-EPS bridge + consensus decomposition.

Pure and offline; derived only from as-reported financials (+ consensus forward EPS). The base
case is a MECHANICAL trailing-trend baseline (backward-looking), NOT a forward judgment — its
value is transparency (a number whose math you can see) and attribution (why it differs from the
Street), not superior forecasting.
"""
from __future__ import annotations

from datetime import date

from saturn.analytics.metrics import _annual_periods, _fact, _index, _ttm_or_fy
from saturn.models import DriverModel, Provenance

_MODEL = "Saturn (model)"
_EXTREME_GROWTH = 0.60   # a consensus-implied growth beyond this is flagged low-confidence


def _revenue_cagr_3y(idx, latest_fy: str) -> float | None:
    cur = _fact(idx, "Revenues", latest_fy)
    prev = _fact(idx, "Revenues", f"FY{int(latest_fy[2:]) - 3}")
    if not cur or not prev or cur.value <= 0 or prev.value <= 0:
        return None
    return (cur.value / prev.value) ** (1 / 3) - 1


def compute_driver_model(fundamentals, quote, consensus) -> DriverModel | None:
    """Trailing-trend forward EPS + consensus two-lens decomposition. Soft-returns None when a
    required input is missing. `quote` is reserved for the FCF bridge / P-E cross-check in later
    slices (unused here). Consensus fields populate only when a consensus forward EPS exists."""
    idx = _index(fundamentals)
    annual = _annual_periods(idx)
    if not annual:
        return None
    latest_fy = annual[0]
    rev = _ttm_or_fy(idx, "Revenues")
    ni = _ttm_or_fy(idx, "NetIncomeLoss")
    shares_fact = _fact(idx, "WeightedAverageSharesDiluted", latest_fy)
    if rev is None or ni is None or shares_fact is None or rev[0] <= 0 or shares_fact.value <= 0:
        return None
    rev_ttm, ni_ttm, shares = rev[0], ni[0], shares_fact.value
    margin = ni_ttm / rev_ttm

    caveats: list[str] = []
    low_conf = False
    g = _revenue_cagr_3y(idx, latest_fy)
    if g is None:
        g = 0.0
        caveats.append("no 3-year revenue history; growth assumed 0%")
        low_conf = True
    if margin <= 0:
        caveats.append("trailing net margin is non-positive; the trend-EPS bridge is unreliable")
        low_conf = True

    saturn_eps = rev_ttm * (1 + g) * margin / shares

    consensus_eps = consensus.forward_eps if consensus is not None else None
    eps_gap = eps_gap_pct = implied_g = implied_m = None
    if consensus_eps:
        eps_gap = saturn_eps - consensus_eps
        eps_gap_pct = eps_gap / abs(consensus_eps)
        if margin > 0:
            implied_g = (consensus_eps * shares / margin) / rev_ttm - 1        # Lens A
        implied_m = consensus_eps * shares / (rev_ttm * (1 + g))               # Lens B
        if implied_g is not None and abs(implied_g) > _EXTREME_GROWTH:
            caveats.append(f"consensus implies revenue growth of {implied_g:.0%} — extreme vs trend")
            low_conf = True

    return DriverModel(
        horizon="NTM",
        saturn_eps=saturn_eps,
        trailing_revenue_growth=g,
        trailing_net_margin=margin,
        shares=shares,
        consensus_eps=consensus_eps,
        eps_gap=eps_gap,
        eps_gap_pct=eps_gap_pct,
        consensus_implied_growth=implied_g,
        consensus_implied_margin=implied_m,
        low_confidence=low_conf,
        caveats=caveats,
        provenance=Provenance(source=_MODEL, as_of=date.today()),
    )
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q` → PASS (5 tests).
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/driver.py tests/analytics/test_driver.py
git commit -m "feat(driver): trailing-trend forward-EPS bridge + consensus two-lens decomposition"
```

---

### Task 3: Wire `compute_driver_model` into the dossier builders

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py`

- [ ] **Step 1: Write the failing test** — APPEND to `tests/ingestion/test_dossier.py`:

```python
def test_mock_dossier_has_driver_model():
    from saturn.ingestion.dossier import _mock_dossier
    d = _mock_dossier("NVDA")
    assert d.driver_model is not None
    assert d.driver_model.saturn_eps is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -q -k "driver_model"`
Expected: FAIL (`d.driver_model` is None).

- [ ] **Step 3: Implement**

In `saturn/ingestion/dossier.py`, add the import near the `compute_forward` import (line ~15):
```python
from saturn.analytics.driver import compute_driver_model
```

There are TWO places that assign `dossier.derived_metrics = compute_metrics(...) + compute_forward(...)` — one in the mock-dossier builder and one in `build_dossier`. Find EACH such line (grep `dossier.derived_metrics =`) and add, immediately AFTER it:
```python
    dossier.driver_model = compute_driver_model(dossier.fundamentals, dossier.quote, dossier.consensus)
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green. (The mock dossier's fundamentals include Revenues/NetIncomeLoss/WeightedAverageSharesDiluted, so `compute_driver_model` returns a model; if a real-path test lacks those it just yields None, which is fine.)

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(dossier): attach driver_model in the mock and real builders"
```

---

### Task 4: Agent context + synthesist prompt nudge

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/test_equity_research_workflow.py`:

```python
def test_company_context_includes_driver_model():
    from saturn.workflows.equity_research import _company_context
    from saturn.ingestion.dossier import _mock_dossier
    ctx = _company_context(_mock_dossier("NVDA"))
    assert "DRIVER MODEL" in ctx and "Saturn forward EPS" in ctx


def test_synthesize_system_references_driver_gap():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    assert "driver" in SYNTHESIZE_SYSTEM.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q -k "driver"`
Expected: FAIL (context has no DRIVER MODEL block; prompt has no "driver").

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py`, in `_company_context`, add a DRIVER MODEL block. Insert it right AFTER the FORWARD/reverse-DCF block (search for the line that appends the `"\nFORWARD / EXPECTATIONS ..."` header and its loop; add this after that block, before the consensus block):

```python
    dm = dossier.driver_model
    if dm is not None:
        lines.append("\nDRIVER MODEL (Saturn trailing-trend forward EPS; mechanical baseline, not a forecast):")
        lines.append(f"- Saturn forward EPS ({dm.horizon}): {dm.saturn_eps:.2f} "
                     f"(rev growth {dm.trailing_revenue_growth:+.1%}, net margin {dm.trailing_net_margin:.1%})")
        if dm.consensus_eps is not None:
            gap = f"{dm.eps_gap:+.2f}" if dm.eps_gap is not None else "n/a"
            lines.append(f"- vs consensus EPS {dm.consensus_eps:.2f}: gap {gap}"
                         + (f" ({dm.eps_gap_pct:+.0%})" if dm.eps_gap_pct is not None else ""))
            if dm.consensus_implied_growth is not None:
                lines.append(f"- consensus implies rev growth {dm.consensus_implied_growth:+.1%} (at trailing margin)")
            if dm.consensus_implied_margin is not None:
                lines.append(f"- consensus implies net margin {dm.consensus_implied_margin:.1%} (at trailing growth)")
        if dm.low_confidence:
            lines.append(f"  NOTE: driver model LOW CONFIDENCE — {'; '.join(dm.caveats)}")
```

Then, in `saturn/agents/synthesist.py`, extend `SYNTHESIZE_SYSTEM`. Find the sentence that ends the rationale-framing instruction (…"the system derives and labels the stance deterministically from your base-case return vs consensus.") and append, right after it (inside the same string literal):
```python
    " When a DRIVER MODEL is present in the data, cite its gap in the variant/rationale — e.g. "
    "the Street's EPS needs revenue growth or margin the trailing trend does not support. "
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py saturn/agents/synthesist.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): render driver model into agent context + synthesist cites the gap"
```

---

### Task 5: Render the `### Driver Bridge` subsection under §2

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/test_markdown_report.py`:

```python
def _driver_model(low=False):
    from saturn.models import DriverModel, Provenance
    return DriverModel(saturn_eps=2.15, trailing_revenue_growth=0.077, trailing_net_margin=0.10,
                       shares=50.0, consensus_eps=2.50, eps_gap=-0.35, eps_gap_pct=-0.14,
                       consensus_implied_growth=0.25, consensus_implied_margin=0.116,
                       low_confidence=low, caveats=(["trailing net margin is non-positive"] if low else []),
                       provenance=Provenance(source="Saturn (model)"))


def test_render_driver_bridge_subsection():
    report = _sample_report()
    report.company.driver_model = _driver_model()
    md = render(report)
    assert "### Driver Bridge" in md
    assert "$2.15" in md and "$2.50" in md            # Saturn EPS + consensus EPS
    assert "+25.0%" in md or "+25%" in md              # Lens A implied growth
    # subsection sits inside §2 (before §3)
    assert md.index("### Driver Bridge") < md.index("## 3.")
    assert md.index("## 2. Alpha Thesis") < md.index("### Driver Bridge")


def test_render_driver_bridge_absent_when_none():
    report = _sample_report()
    report.company.driver_model = None
    assert "### Driver Bridge" not in render(report)


def test_render_driver_bridge_low_confidence_caveat():
    report = _sample_report()
    report.company.driver_model = _driver_model(low=True)
    assert "Low confidence" in render(report) or "LOW CONFIDENCE" in render(report)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q -k "driver_bridge"`
Expected: FAIL (no `### Driver Bridge`).

- [ ] **Step 3: Implement**

In `saturn/reports/markdown_report.py`, add a helper before `def render(`:

```python
def _render_driver_bridge(dm) -> list[str]:
    """Render Saturn's trailing-trend forward-EPS bridge as a §2 subsection. Empty when None."""
    if dm is None:
        return []
    out = [f"### Driver Bridge{' (Low confidence)' if dm.low_confidence else ''}", ""]
    out.append(f"- **Saturn forward EPS ({dm.horizon}):** ${dm.saturn_eps:,.2f} "
               f"(rev growth {dm.trailing_revenue_growth:+.1%}, net margin {dm.trailing_net_margin:.1%})")
    if dm.consensus_eps is not None:
        gap = f"${dm.eps_gap:+,.2f}" if dm.eps_gap is not None else "N/A"
        pct = f" ({dm.eps_gap_pct:+.0%})" if dm.eps_gap_pct is not None else ""
        out.append(f"- **vs consensus EPS ${dm.consensus_eps:,.2f}:** gap {gap}{pct}")
        if dm.consensus_implied_growth is not None:
            out.append(f"- Consensus implies **rev growth {dm.consensus_implied_growth:+.1%}** (at trailing margin)")
        if dm.consensus_implied_margin is not None:
            out.append(f"- …or **net margin {dm.consensus_implied_margin:.1%}** (at trailing growth)")
    if dm.caveats:
        out.append(f"- _{'; '.join(dm.caveats)}_")
    out.append("")
    return out
```

In `render()`, find where the alpha block is emitted right after the Executive Summary + high-severity banner:
```python
    if report.alpha_thesis is not None:
        out += _render_alpha(report.alpha_thesis)
    else:
        out += ["## 2. Alpha Thesis", "", "_Alpha thesis unavailable this run._", ""]
```
Immediately AFTER that if/else (so the subsection nests under §2, before §3), add:
```python
    out += _render_driver_bridge(report.company.driver_model)
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): render §2 Driver Bridge subsection (Saturn EPS vs consensus, two lenses)"
```

---

## Final verification (live)

Regenerate a report and confirm the driver model end-to-end:

```bash
.venv/Scripts/python.exe -m saturn.cli research MSFT
```

In `reports/MSFT_<date>.md`:
1. A **`### Driver Bridge`** subsection under §2 shows Saturn's forward EPS with its inputs (rev growth, net margin), the gap vs consensus EPS, and both lenses ("consensus implies rev growth X% … or net margin Y%").
2. The §2 **variant/rationale now cites the driver gap** (e.g. "the Street's number needs growth the trailing trend doesn't support").
3. The **stance line is unchanged** (still price-based `(base +X% vs consensus target +Y%)`), and the scenario table is unaffected.
4. If the company is a loss-maker / has no 3-yr history, the bridge shows a **Low confidence** caveat rather than a bogus number.

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §2 bridge → Task 2; §3 two-lens decomposition → Task 2; §4 model+dossier → Tasks 1/3; §5 confidence/soft-fail → Task 2 (tests for negative margin + no-history); §6 render+context+prompt → Tasks 4/5 (stance untouched — no stance code is modified in any task); §7 tests distributed.
- **Deliberate deviation from spec §4 signature:** `compute_driver_model(fundamentals, quote, consensus)` keeps `quote` per the spec but it is **unused in Slice 1** (documented in the docstring as reserved for the FCF/P-E cross-check in later slices) — matching the dossier call site `compute_driver_model(dossier.fundamentals, dossier.quote, dossier.consensus)` and avoiding a spec/code signature mismatch.
- **Type consistency:** `DriverModel` fields identical across models/driver/render/context; `compute_driver_model(fundamentals, quote, consensus) -> DriverModel | None`; `_render_driver_bridge(dm)`; `report.company.driver_model` used in render. All aligned.
- **No placeholders:** every step has complete code.

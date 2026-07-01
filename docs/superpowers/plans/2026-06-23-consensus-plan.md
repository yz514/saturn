# yfinance Consensus Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a best-effort `"yfinance (estimate)"` analyst-consensus view (forward P/E, PEG, price targets + upside, rating, last earnings surprise) that is validated against our verified as-reported baseline before anything is surfaced, with implausible values dropped and explained.

**Architecture:** A new `saturn/ingestion/consensus.py` with a thin `fetch_consensus(ticker)` (yfinance I/O) and a pure `validate_consensus(raw, fundamentals, quote)` (the heavily-tested centerpiece). `build_dossier` routes the fetch through the soft-fail dispatcher and runs validation after EDGAR+quote, attaching a `ConsensusSnapshot` to the dossier. The report renders it as a sub-section under §6, the LLM context as a caveated block, and `saturn doctor` gets a reachability check.

**Tech Stack:** Python 3.13, Pydantic v2, yfinance, Typer, pytest. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-23-consensus-design.md`

---

## File Structure

- **Create:** `saturn/ingestion/consensus.py` (`RawConsensus`, thresholds, `fetch_consensus`, `validate_consensus`); `tests/ingestion/test_consensus.py`
- **Modify:** `saturn/models.py` (`ConsensusSnapshot`, `CompanyDossier.consensus`); `saturn/ingestion/dossier.py` (route + validate + attach, real + mock); `saturn/reports/markdown_report.py` (Consensus sub-section under §6); `saturn/workflows/equity_research.py` (context block); `saturn/diagnostics.py` + `saturn/cli.py` (`check_consensus`); touched report/context/doctor/dossier tests.

**No top-level section renumbering:** the Consensus block is a `###` sub-section under §6 Key Metrics (alongside the Forward / Expectations sub-table), so sections 7–16 are unchanged.

---

## Task 1: Models — ConsensusSnapshot + dossier field

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:
```python
def test_consensus_snapshot_model():
    from saturn.models import ConsensusSnapshot, Provenance, CompanyDossier
    from datetime import date

    c = ConsensusSnapshot(
        forward_pe=28.0, forward_eps=32.0, target_mean=1000.0, target_upside_pct=0.1,
        rating="buy", n_analysts=40, last_eps_surprise_pct=0.05,
        provenance=Provenance(source="yfinance (estimate)"),
        rejected=["forward_eps: rejected — implies +300%"],
    )
    assert c.forward_pe == 28.0 and c.rating == "buy"
    assert c.rejected[0].startswith("forward_eps")
    assert c.peg is None  # optional default

    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 6, 23))
    assert d.consensus is None
    d.consensus = c
    assert d.consensus.rating == "buy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py::test_consensus_snapshot_model -v`
Expected: FAIL with `ImportError: cannot import name 'ConsensusSnapshot'`.

- [ ] **Step 3: Implement**

In `saturn/models.py`, after `MacroSnapshot` add:
```python
class ConsensusSnapshot(BaseModel):
    """Validated, best-effort analyst consensus (yfinance). A distinct epistemic
    class: external estimate data, not as-reported and not a Saturn model output."""

    forward_eps: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    target_upside_pct: float | None = None
    rating: str | None = None
    n_analysts: int | None = None
    last_eps_surprise_pct: float | None = None
    provenance: Provenance
    rejected: list[str] = Field(default_factory=list)
```
And in `CompanyDossier`, add the field (next to `macro`):
```python
    consensus: ConsensusSnapshot | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models.py
git commit -m "feat(models): ConsensusSnapshot + dossier.consensus"
```
(End every commit message with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.)

---

## Task 2: validate_consensus — the pure validation centerpiece

**Files:**
- Create: `saturn/ingestion/consensus.py`, `tests/ingestion/test_consensus.py`

This task is pure logic (no network). It builds `RawConsensus`, the thresholds, and `validate_consensus`. `fetch_consensus` comes in Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingestion/test_consensus.py`:
```python
from saturn.ingestion.consensus import RawConsensus, validate_consensus
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _fund(eps_by_fy):
    return Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=v, unit="USD/shares",
                      fiscal_period=p, provenance=PROV)
        for p, v in eps_by_fy.items()
    ])


def _quote(price):
    return Quote(price=price, market_cap=None, currency="USD", provenance=Provenance(source="yfinance"))


def test_clean_case_all_fields_pass():
    raw = RawConsensus(forward_eps=9.60, forward_pe=30.66, peg=2.4,
                       target_mean=314.0, target_high=360.0, target_low=250.0, rating="buy",
                       n_analysts=42, last_actual_eps=2.40, last_estimate_eps=2.35)
    c = validate_consensus(raw, _fund({"FY2024": 8.27}), _quote(294.3))
    assert c.forward_eps == 9.60 and abs(c.forward_pe - 30.66) < 1e-6
    assert abs(c.target_upside_pct - (314.0 / 294.3 - 1)) < 1e-9
    assert c.rating == "buy" and c.n_analysts == 42
    assert c.last_eps_surprise_pct is not None
    assert c.rejected == []
    assert c.provenance.source == "yfinance (estimate)"


def test_avgo_split_case_rejects_eps_keeps_targets():
    # forward EPS 19.35 vs verified trailing 4.80 -> +303% -> EPS trio rejected;
    # price targets are sane and survive (per-field granularity).
    raw = RawConsensus(forward_eps=19.35, forward_pe=19.7, peg=0.7,
                       target_mean=522.0, target_high=650.0, target_low=216.0,
                       rating="strong_buy", n_analysts=45)
    c = validate_consensus(raw, _fund({"FY2025": 4.80}), _quote(380.0))
    assert c.forward_eps is None and c.forward_pe is None and c.peg is None
    assert any("forward_eps" in r for r in c.rejected)
    assert c.target_mean == 522.0 and c.rating == "strong_buy"


def test_internal_inconsistency_rejects_eps_trio():
    # forward_pe disagrees with price/forward_eps -> reject
    raw = RawConsensus(forward_eps=10.0, forward_pe=50.0, target_mean=None, n_analysts=10)
    c = validate_consensus(raw, _fund({"FY2024": 9.0}), _quote(100.0))  # implied pe = 10
    assert c.forward_pe is None
    assert any("forward_pe" in r or "forward_eps" in r for r in c.rejected)


def test_target_out_of_band_dropped():
    raw = RawConsensus(target_mean=5000.0, target_high=6000.0, target_low=4000.0, n_analysts=10)
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))  # 5000 > 5x*100
    assert c.target_mean is None and any("price_target" in r for r in c.rejected)


def test_too_few_analysts_drops_targets_and_rating():
    raw = RawConsensus(target_mean=110.0, target_high=120.0, target_low=100.0,
                       rating="buy", n_analysts=2)
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))
    assert c.target_mean is None and c.rating is None


def test_absurd_surprise_dropped():
    raw = RawConsensus(last_actual_eps=10.0, last_estimate_eps=1.0)  # +900%
    c = validate_consensus(raw, _fund({"FY2024": 5.0}), _quote(100.0))
    assert c.last_eps_surprise_pct is None and any("surprise" in r for r in c.rejected)


def test_missing_baseline_rejects_eps_but_keeps_targets():
    raw = RawConsensus(forward_eps=9.0, forward_pe=11.0,
                       target_mean=110.0, target_high=120.0, target_low=100.0, n_analysts=10)
    c = validate_consensus(raw, _fund({}), _quote(100.0))  # no EPS fact
    assert c.forward_eps is None and any("forward_eps" in r for r in c.rejected)
    assert c.target_mean == 110.0


def test_negative_trailing_eps_rejects_forward():
    raw = RawConsensus(forward_eps=2.0, forward_pe=50.0)
    c = validate_consensus(raw, _fund({"FY2024": -1.0}), _quote(100.0))
    assert c.forward_eps is None and any("forward_eps" in r for r in c.rejected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.consensus'`.

- [ ] **Step 3: Implement**

Create `saturn/ingestion/consensus.py`:
```python
"""yfinance analyst-consensus adapter: thin fetch + a pure validator that gates every
value against our verified as-reported baseline. Best-effort 'estimate' provenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from saturn.models import ConsensusSnapshot, Fundamentals, Provenance, Quote

logger = logging.getLogger(__name__)

# Validation thresholds (tunable in one place).
EPS_GROWTH_BAND = (-0.60, 1.50)   # forward EPS vs verified trailing EPS
TARGET_PRICE_BAND = (0.2, 5.0)    # price targets as a multiple of current price
MIN_ANALYSTS = 3
MAX_SURPRISE = 2.0                 # |last EPS surprise|
PE_CONSISTENCY_TOL = 0.05         # |forward_pe - price/forward_eps| / (price/forward_eps)

_SOURCE = "yfinance (estimate)"


@dataclass
class RawConsensus:
    """Unvalidated raw fields read from yfinance."""
    forward_eps: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    rating: str | None = None
    n_analysts: int | None = None
    last_actual_eps: float | None = None
    last_estimate_eps: float | None = None


def _latest_fy_eps(fundamentals: Fundamentals | None) -> float | None:
    if not fundamentals:
        return None
    rows = []
    for f in fundamentals.facts:
        p = f.fiscal_period or ""
        if f.concept == "EarningsPerShareDiluted" and p.startswith("FY") and f.value is not None:
            try:
                rows.append((int(p[2:]), f.value))
            except ValueError:
                continue
    return max(rows, key=lambda t: t[0])[1] if rows else None


def validate_consensus(
    raw: RawConsensus, fundamentals: Fundamentals | None, quote: Quote | None
) -> ConsensusSnapshot:
    """Gate each raw consensus field against the verified baseline; surface only what
    passes, recording a human-readable reason for each rejection."""
    rejected: list[str] = []
    snap = ConsensusSnapshot(provenance=Provenance(source=_SOURCE, as_of=date.today(), retrieved_at=date.today()))
    price = quote.price if quote else None
    trailing_eps = _latest_fy_eps(fundamentals)

    # --- forward EPS / forward PE / PEG ---
    fe = raw.forward_eps
    if fe is not None:
        if price is None:
            rejected.append("forward_eps: no price to validate against")
        elif trailing_eps is None or trailing_eps <= 0:
            rejected.append(f"forward_eps: no positive verified trailing EPS to validate against (got {trailing_eps})")
        else:
            growth = fe / trailing_eps - 1
            lo, hi = EPS_GROWTH_BAND
            implied_pe = price / fe if fe else None
            inconsistent = (
                raw.forward_pe is not None and implied_pe
                and abs(raw.forward_pe - implied_pe) > PE_CONSISTENCY_TOL * implied_pe
            )
            if not (lo <= growth <= hi):
                rejected.append(
                    f"forward_eps: rejected — implies {growth:+.0%} vs verified trailing "
                    f"{trailing_eps:.2f} (outside [{lo:+.0%}, {hi:+.0%}])"
                )
            elif inconsistent:
                rejected.append(
                    f"forward_pe: rejected — {raw.forward_pe} inconsistent with price/forward_eps {implied_pe:.1f}"
                )
            else:
                snap.forward_eps = fe
                snap.forward_pe = raw.forward_pe if raw.forward_pe is not None else implied_pe
                snap.peg = raw.peg

    # --- price targets ---
    tm, th, tl, na = raw.target_mean, raw.target_high, raw.target_low, raw.n_analysts
    if tm is not None:
        if price is None:
            rejected.append("price_target: no price to validate against")
        else:
            lo, hi = TARGET_PRICE_BAND

            def _in_band(v):
                return v is None or (lo * price <= v <= hi * price)

            ordered = (tl is None or th is None) or (tl <= tm <= th)
            if not (_in_band(tm) and _in_band(th) and _in_band(tl)):
                rejected.append(f"price_target: rejected — outside [{lo}x, {hi}x] of price {price}")
            elif not ordered:
                rejected.append("price_target: rejected — low/mean/high not ordered")
            elif na is None or na < MIN_ANALYSTS:
                rejected.append(f"price_target: rejected — only {na} analysts (< {MIN_ANALYSTS})")
            else:
                snap.target_mean, snap.target_high, snap.target_low = tm, th, tl
                snap.target_upside_pct = tm / price - 1

    # --- rating ---
    if raw.rating:
        if raw.n_analysts is not None and raw.n_analysts >= MIN_ANALYSTS:
            snap.rating = raw.rating
            snap.n_analysts = raw.n_analysts
        else:
            rejected.append(f"rating: withheld — only {raw.n_analysts} analysts (< {MIN_ANALYSTS})")

    # --- last EPS surprise ---
    a, e = raw.last_actual_eps, raw.last_estimate_eps
    if a is not None and e:
        surprise = (a - e) / abs(e)
        if abs(surprise) <= MAX_SURPRISE:
            snap.last_eps_surprise_pct = surprise
        else:
            rejected.append(f"eps_surprise: rejected — {surprise:+.0%} implausible")

    snap.rejected = rejected
    return snap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): pure validate_consensus with baseline gates"
```

---

## Task 3: fetch_consensus — thin yfinance reader

**Files:**
- Modify: `saturn/ingestion/consensus.py`
- Test: `tests/ingestion/test_consensus.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_consensus.py`:
```python
def test_fetch_consensus_maps_info_fields(monkeypatch):
    import saturn.ingestion.consensus as cons

    class _FakeTicker:
        def __init__(self, t): pass
        @property
        def info(self):
            return {"forwardEps": 9.6, "forwardPE": 30.6, "pegRatio": 2.4,
                    "targetMeanPrice": 314.0, "targetHighPrice": 360.0, "targetLowPrice": 250.0,
                    "recommendationKey": "buy", "numberOfAnalystOpinions": 42}
        @property
        def earnings_history(self):
            return None

    import types
    monkeypatch.setattr(cons, "yf", types.SimpleNamespace(Ticker=_FakeTicker), raising=False)
    raw = cons.fetch_consensus("AAPL")
    assert raw.forward_eps == 9.6 and raw.forward_pe == 30.6 and raw.peg == 2.4
    assert raw.target_mean == 314.0 and raw.rating == "buy" and raw.n_analysts == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py::test_fetch_consensus_maps_info_fields -v`
Expected: FAIL (`fetch_consensus` / `yf` not defined).

- [ ] **Step 3: Implement**

In `saturn/ingestion/consensus.py`, add the import near the top (after the stdlib imports) and the fetcher. The import is module-level so tests can monkeypatch `cons.yf`:
```python
import yfinance as yf  # noqa: E402  (kept module-level so tests can patch saturn.ingestion.consensus.yf)
```
Then add `fetch_consensus`:
```python
def fetch_consensus(ticker: str) -> RawConsensus:
    """Read the reliable .info summary fields + last earnings surprise from yfinance.
    Thin and defensive: returns whatever is present; never raises on a missing field."""
    handle = yf.Ticker(ticker)
    info = handle.info or {}
    raw = RawConsensus(
        forward_eps=info.get("forwardEps"),
        forward_pe=info.get("forwardPE"),
        peg=info.get("pegRatio") if info.get("pegRatio") is not None else info.get("trailingPegRatio"),
        target_mean=info.get("targetMeanPrice"),
        target_high=info.get("targetHighPrice"),
        target_low=info.get("targetLowPrice"),
        rating=info.get("recommendationKey"),
        n_analysts=info.get("numberOfAnalystOpinions"),
    )
    # last earnings surprise (best-effort; column names vary across yfinance versions)
    try:
        hist = handle.earnings_history
        if hist is not None and len(hist) and "epsActual" in hist.columns and "epsEstimate" in hist.columns:
            row = hist.dropna(subset=["epsActual", "epsEstimate"]).tail(1)
            if len(row):
                raw.last_actual_eps = float(row["epsActual"].iloc[0])
                raw.last_estimate_eps = float(row["epsEstimate"].iloc[0])
    except Exception as exc:  # noqa: BLE001 - surprise is optional
        logger.debug("consensus earnings_history unavailable for %s: %s", ticker, exc)
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_consensus.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/consensus.py tests/ingestion/test_consensus.py
git commit -m "feat(consensus): thin yfinance fetch_consensus reader"
```

---

## Task 4: Attach consensus in build_dossier + mock

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_dossier.py`:
```python
def test_build_dossier_validates_and_attaches_consensus(monkeypatch):
    from saturn.models import Fundamentals, FinancialFact, Provenance, Quote
    from saturn.ingestion.consensus import RawConsensus

    prov = Provenance(source="SEC EDGAR")
    fund = Fundamentals(facts=[
        FinancialFact(concept="EarningsPerShareDiluted", value=8.27, unit="USD/shares",
                      fiscal_period="FY2024", provenance=prov),
    ])
    quote = Quote(price=294.3, market_cap=1.0, currency="USD", provenance=Provenance(source="yfinance"))
    raw = RawConsensus(forward_eps=9.6, forward_pe=30.66, target_mean=314.0,
                       target_high=360.0, target_low=250.0, rating="buy", n_analysts=42)
    monkeypatch.setattr("saturn.ingestion.dossier.fetch_consensus", lambda t: raw)

    d = build_dossier(
        "X",
        quote_fn=lambda t, *, mock: quote,
        edgar_fn=lambda t: {"fundamentals": fund, "filing_sections": [], "material_events": [], "name": "X", "cik": "1"},
        fred_fn=lambda t: None,
    )
    assert d.consensus is not None
    assert d.consensus.forward_eps == 9.6 and d.consensus.rating == "buy"
    assert d.consensus.provenance.source == "yfinance (estimate)"


def test_mock_dossier_has_consensus():
    d = _mock_dossier("NVDA")
    assert d.consensus is not None and d.consensus.rating is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py::test_build_dossier_validates_and_attaches_consensus -v`
Expected: FAIL (`d.consensus` is None / `fetch_consensus` not imported in dossier).

- [ ] **Step 3: Implement**

In `saturn/ingestion/dossier.py`:
- Add imports:
```python
from saturn.ingestion.consensus import fetch_consensus, validate_consensus, RawConsensus
from saturn.models import ConsensusSnapshot
```
- In `build_dossier`, place this **after the EDGAR-result extraction block** (where
  `fundamentals` is assigned from `edgar_result`) and **immediately before**
  `dossier = CompanyDossier(...)`, so both `fundamentals` and `quote` are in scope:
```python
    def _consensus():
        return fetch_consensus(ticker)

    raw_consensus, gap = route_to_source("consensus", _consensus)
    if gap:
        gaps.append(gap)
    consensus = (
        validate_consensus(raw_consensus, fundamentals, quote)
        if isinstance(raw_consensus, RawConsensus)
        else None
    )
```
- Pass `consensus=consensus` into the `CompanyDossier(...)` constructor (next to `macro=...`).
- In `_mock_dossier`, build a sample snapshot and set it on the dossier before returning (next to where `derived_metrics` is set):
```python
    dossier.consensus = ConsensusSnapshot(
        forward_eps=32.0, forward_pe=28.0, peg=1.5,
        target_mean=1000.0, target_high=1200.0, target_low=800.0, target_upside_pct=1000.0 / 900.0 - 1,
        rating="buy", n_analysts=40, last_eps_surprise_pct=0.05,
        provenance=Provenance(source="yfinance (estimate, mock)", as_of=date.today()),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(dossier): route + validate + attach consensus (real + mock)"
```

---

## Task 5: Consensus sub-section in the report

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_markdown_report.py`:
```python
def test_render_consensus_subsection():
    from saturn.models import ConsensusSnapshot, Provenance
    report = _sample_report()
    report.company.consensus = ConsensusSnapshot(
        forward_pe=28.0, peg=1.5, target_mean=1000.0, target_upside_pct=0.11,
        rating="buy", n_analysts=40, last_eps_surprise_pct=0.05,
        provenance=Provenance(source="yfinance (estimate)"),
        rejected=["forward_eps: rejected — implies +266% vs verified trailing 4.80"],
    )
    md = render(report)
    assert "Consensus / Analyst Expectations" in md
    assert "28.0x" in md          # forward P/E
    assert "buy" in md and "40" in md
    assert "estimate" in md.lower()  # the best-effort caveat
    assert "rejected" in md and "forward_eps" in md  # rejection list surfaced


def test_render_consensus_absent():
    report = _sample_report()
    report.company.consensus = None
    md = render(report)
    assert "_No analyst consensus available._" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py::test_render_consensus_subsection -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `saturn/reports/markdown_report.py`, inside `render`, immediately after the Forward / Expectations sub-table block (the `if _forward:` block that ends the §6 content, before `out += ["## 7. ...`), add the consensus sub-section:
```python
    out += ["### Consensus / Analyst Expectations (estimate)", ""]
    cons = c.consensus
    _has = cons and any(v is not None for v in (
        cons.forward_pe, cons.target_mean, cons.rating, cons.last_eps_surprise_pct))
    if _has:
        out.append("| Field | Value |")
        out.append("| --- | --- |")
        if cons.forward_pe is not None:
            out.append(f"| Forward P/E | {cons.forward_pe:.1f}x |")
        if cons.peg is not None:
            out.append(f"| PEG | {cons.peg:.2f} |")
        if cons.target_mean is not None:
            rng = f" (range {_fmt_money(cons.target_low)}–{_fmt_money(cons.target_high)})" if cons.target_low is not None else ""
            up = f", {cons.target_upside_pct * 100:+.1f}% vs price" if cons.target_upside_pct is not None else ""
            out.append(f"| Price target (mean) | {_fmt_money(cons.target_mean)}{rng}{up} |")
        if cons.rating is not None:
            out.append(f"| Analyst rating | {cons.rating} ({cons.n_analysts} analysts) |")
        if cons.last_eps_surprise_pct is not None:
            out.append(f"| Last EPS surprise | {cons.last_eps_surprise_pct * 100:+.1f}% |")
        out.append("")
        out.append(
            "_Best-effort analyst estimates from yfinance; values failing validation "
            "against as-reported data are dropped. Not as-reported._"
        )
        if cons.rejected:
            out.append("")
            out += [f"- rejected — {r}" for r in cons.rejected]
    else:
        out.append("_No analyst consensus available._")
    out.append("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -v`
Expected: PASS (new tests + all existing; no top-level renumber so sections 7–16 tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): Consensus / Analyst Expectations sub-section"
```

---

## Task 6: Consensus block in the LLM context

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_equity_research.py`:
```python
def test_company_context_includes_consensus_block():
    from datetime import date as _date
    from saturn.models import CompanyDossier, ConsensusSnapshot, Provenance
    from saturn.workflows.equity_research import _company_context

    d = CompanyDossier(ticker="X", name="X", generated_at=_date(2026, 6, 23))
    d.consensus = ConsensusSnapshot(
        forward_pe=28.0, target_mean=1000.0, target_upside_pct=0.11, rating="buy", n_analysts=40,
        provenance=Provenance(source="yfinance (estimate)"),
        rejected=["forward_eps: rejected — implies +266%"],
    )
    ctx = _company_context(d)
    assert "CONSENSUS" in ctx
    assert "forward_pe" in ctx and "28.0" in ctx
    assert "estimate" in ctx.lower()       # the unreliability caveat
    assert "rejected" in ctx               # withheld values explained
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py::test_company_context_includes_consensus_block -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py` `_company_context`, after the FORWARD / EXPECTATIONS block (the `if _forward:` block added in the forward-metrics slice), add:
```python
    cons = dossier.consensus
    if cons is not None:
        lines.append("\nCONSENSUS / ANALYST EXPECTATIONS (yfinance estimate; may be unreliable):")
        for label, val in (
            ("forward_pe", cons.forward_pe), ("peg", cons.peg),
            ("target_mean", cons.target_mean), ("target_upside_pct", cons.target_upside_pct),
            ("rating", cons.rating), ("n_analysts", cons.n_analysts),
            ("last_eps_surprise_pct", cons.last_eps_surprise_pct),
        ):
            if val is not None:
                lines.append(f"- {label}: {val}")
        if cons.rejected:
            lines.append(f"- rejected (failed validation, withheld): {'; '.join(cons.rejected)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): surface validated consensus in analyst context"
```

---

## Task 7: `saturn doctor` consensus check

**Files:**
- Modify: `saturn/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_diagnostics.py`:
```python
def test_check_consensus_ok(monkeypatch):
    from saturn.diagnostics import check_consensus
    from saturn.ingestion.consensus import RawConsensus
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus",
                        lambda t: RawConsensus(forward_pe=30.0, rating="buy", n_analysts=40))
    r = check_consensus("AAPL")
    assert r.ok and "forward_pe" in r.detail.lower() or r.ok


def test_check_consensus_empty(monkeypatch):
    from saturn.diagnostics import check_consensus
    from saturn.ingestion.consensus import RawConsensus
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus", lambda t: RawConsensus())
    r = check_consensus("ZZZZ")
    assert not r.ok


def test_check_consensus_never_raises(monkeypatch):
    from saturn.diagnostics import check_consensus
    def boom(t):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus", boom)
    r = check_consensus("AAPL")
    assert not r.ok and "yfinance down" in r.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_diagnostics.py::test_check_consensus_ok -v`
Expected: FAIL (`check_consensus` not defined).

- [ ] **Step 3: Implement**

In `saturn/diagnostics.py`:
- Add import: `from saturn.ingestion.consensus import fetch_consensus`.
- Add the check (after `check_fred`):
```python
def check_consensus(ticker: str) -> CheckResult:
    try:
        raw = fetch_consensus(ticker)
        present = [k for k, v in vars(raw).items() if v is not None]
        if present:
            return CheckResult(name="consensus", ok=True, detail=f"{len(present)} fields (forward_pe={raw.forward_pe})")
        return CheckResult(name="consensus", ok=False, detail="no consensus fields returned")
    except Exception as exc:  # noqa: BLE001 - a check never raises
        return CheckResult(name="consensus", ok=False, detail=str(exc))
```
- In `run_checks`, add `check_consensus(ticker),` to the returned list (after `check_fred()`).
- If any existing test in `tests/test_diagnostics.py` asserts the *number* of checks
  `run_checks` returns (e.g. `len(results) == 4`) or the exact set of check names,
  update it to expect the new consensus check (now 5). Run the file to find such a
  test before committing.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_diagnostics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(doctor): consensus reachability check"
```

---

## Task 8: Full-suite verification + offline smoke test

**Files:**
- Test: full suite

- [ ] **Step 1: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 2: Generate a mock report and eyeball the Consensus sub-section**

Run: `.venv/Scripts/python.exe -m saturn.cli research NVDA --mock`
Open `reports/NVDA_<today>.md`: confirm a `### Consensus / Analyst Expectations (estimate)` sub-section under §6 shows the mock forward P/E / target / rating with the best-effort note, and sections 7–16 are unchanged.

- [ ] **Step 3: (optional, live) confirm validation on a split name**

A live `saturn research AVGO` is the real check: the consensus sub-section should show AVGO's price target + rating but **list `forward_eps` as rejected** (split-scrambled). Run separately; do not block the offline suite on it.

- [ ] **Step 4: Commit any incidental fixes**

```bash
git add -A
git commit -m "test(consensus): full-suite verification fixes"
```

- [ ] **Step 5: Finish the branch**

Use **superpowers:finishing-a-development-branch** (tests must pass first). Likely option: push a stacked PR.

---

## Notes for the implementer

- **Validation is the point.** Never surface a consensus value that fails its gate — drop it and append a human-readable reason to `rejected`. Per-field granularity matters (AVGO's targets survive even when its split-broken EPS is rejected).
- **Provenance class:** consensus is `"yfinance (estimate)"` — a third epistemic class, kept OFF `derived_metrics`, on its own `dossier.consensus`. Always caveat it as best-effort/unreliable in the report and context.
- **No fabrication on missing baseline:** if there's no positive verified trailing EPS, the forward-EPS gate can't run, so the EPS trio is rejected (not surfaced) — targets/rating/surprise (which don't need the baseline) can still pass.
- **Keep `yf` module-level** in `consensus.py` so tests can monkeypatch `saturn.ingestion.consensus.yf`.

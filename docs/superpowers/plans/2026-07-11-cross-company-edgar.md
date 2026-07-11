# Cross-Company EDGAR (Industry / Value-Chain Context) — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps. Design: `docs/superpowers/specs/2026-07-11-cross-company-edgar-design.md`.

**Goal:** Attach a curated set of related public companies' headline as-reported signals (Nvidia/AMD revenue growth, hyperscaler capex, US equipment) to the dossier so the analyst can triangulate the target's demand tailwind. US-filers only; soft-fail.

---

### Task 1: Models

**Files:** `saturn/models.py`; test `tests/test_models.py`.

- [ ] **Failing test:** construct `PeerSummary(ticker="NVDA", role="demand", revenue_growth_yoy=0.6, provenance=Provenance(source="SEC EDGAR"))` and `IndustryContext(peers=[...], note="n", provenance=...)`; assert fields; assert `CompanyDossier(...).industry_context` defaults to `None`.
- [ ] **Implement** (near `MaterialEvent`/before `CompanyDossier`):
```python
class PeerSummary(BaseModel):
    """One value-chain peer's headline as-reported signals (demand/supply proxy)."""
    ticker: str
    role: str                       # demand | supply | peer
    name: str | None = None
    revenue_ttm: float | None = None
    revenue_growth_yoy: float | None = None
    capex: float | None = None
    capex_intensity: float | None = None
    provenance: Provenance


class IndustryContext(BaseModel):
    peers: list[PeerSummary] = Field(default_factory=list)
    note: str = ""
    provenance: Provenance
```
Add to `CompanyDossier`: `industry_context: IndustryContext | None = None`.
- [ ] Run → green; full suite. Commit `feat(models): PeerSummary / IndustryContext + dossier field`.

---

### Task 2: `peers.py` — value-chain map + fetch

**Files:** create `saturn/ingestion/peers.py`, `tests/ingestion/test_peers.py`.

- [ ] **Failing tests** (`tests/ingestion/test_peers.py`):
```python
from saturn.ingestion import peers
from saturn.models import IndustryContext, PeerSummary


def test_peers_for_matches_semiconductor_industry():
    got = peers._peers_for("Semiconductors")
    assert ("NVDA", "demand") in got and ("AMAT", "supply") in got

def test_peers_for_unmapped_industry_is_empty():
    assert peers._peers_for("Restaurants") == []

def test_fetch_industry_context_excludes_self_and_skips_failures(monkeypatch):
    # stub the per-peer summary: NVDA succeeds, AMD returns None (skipped), MU is the target
    def fake_summary(ticker, role):
        return None if ticker == "AMD" else PeerSummary(ticker=ticker, role=role, revenue_growth_yoy=0.5,
                                                        provenance=peers.Provenance(source="SEC EDGAR"))
    monkeypatch.setattr(peers, "_peer_summary", fake_summary)
    ic = peers.fetch_industry_context("MU", "Semiconductors")
    tickers = {p.ticker for p in ic.peers}
    assert "MU" not in tickers          # self excluded
    assert "AMD" not in tickers         # None skipped
    assert "NVDA" in tickers and isinstance(ic, IndustryContext) and ic.note

def test_fetch_industry_context_no_peers_raises(monkeypatch):
    from saturn.ingestion.errors import DataUnavailable
    import pytest
    monkeypatch.setattr(peers, "_peer_summary", lambda t, r: None)
    with pytest.raises(DataUnavailable):
        peers.fetch_industry_context("MU", "Semiconductors")
```

- [ ] **Implement** `saturn/ingestion/peers.py`:
```python
"""Cross-company EDGAR: curated value-chain peers' headline as-reported signals."""
from __future__ import annotations

import logging

from saturn.analytics.metrics import compute_metrics
from saturn.ingestion.edgar import _fetch_companyfacts, _parse_companyfacts
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.identifiers import ticker_to_cik
from saturn.models import IndustryContext, PeerSummary, Provenance

logger = logging.getLogger(__name__)

_AI_COMPUTE_CHAIN = [
    ("NVDA", "demand"), ("AMD", "demand"),
    ("MSFT", "demand"), ("GOOGL", "demand"), ("AMZN", "demand"), ("META", "demand"),
    ("AMAT", "supply"), ("LRCX", "supply"),
]
VALUE_CHAIN = {"semiconductor": _AI_COMPUTE_CHAIN}
_NOTE = ("US-filer value-chain proxies (revenue/capex); excludes foreign filers "
         "(e.g. TSMC/ASML, IFRS) and does not include GPU-unit or HBM-content estimates.")


def _peers_for(industry: str | None) -> list[tuple[str, str]]:
    key = (industry or "").lower()
    for kw, chain in VALUE_CHAIN.items():
        if kw in key:
            return chain
    return []


def _period_rank(period: str) -> tuple[int, int]:
    p = period or ""
    if p.startswith("FY"):
        try:
            return (int(p[2:]), 4)
        except ValueError:
            return (-1, -1)
    try:
        q, fy = p.split()
        return (int(fy[2:]), int(q[1]))
    except (ValueError, IndexError):
        return (-1, -1)


def _latest_metric(ms, name):
    xs = [m for m in ms if m.name == name]
    return max(xs, key=lambda m: _period_rank(m.fiscal_period)).value if xs else None


def _latest_capex(facts):
    xs = [f for f in facts if f.concept == "CapitalExpenditures" and f.value is not None]
    return max(xs, key=lambda f: _period_rank(f.fiscal_period)).value if xs else None


def _peer_summary(ticker: str, role: str) -> PeerSummary | None:
    try:
        fund = _parse_companyfacts(_fetch_companyfacts(ticker_to_cik(ticker)))
        ms = compute_metrics(fund, None)
        return PeerSummary(
            ticker=ticker, role=role,
            revenue_ttm=_latest_metric(ms, "revenue_ttm"),
            revenue_growth_yoy=_latest_metric(ms, "revenue_growth_yoy"),
            capex=_latest_capex(fund.facts),
            capex_intensity=_latest_metric(ms, "capex_intensity"),
            provenance=Provenance(source="SEC EDGAR"),
        )
    except Exception as exc:  # noqa: BLE001 - a peer is optional
        logger.debug("peer %s unavailable: %s", ticker, exc)
        return None


def fetch_industry_context(target_ticker: str, industry: str | None) -> IndustryContext:
    peers = []
    for tk, role in _peers_for(industry):
        if tk.upper() == (target_ticker or "").upper():
            continue
        s = _peer_summary(tk, role)
        if s:
            peers.append(s)
    if not peers:
        raise DataUnavailable(f"no value-chain peers for industry {industry!r}")
    return IndustryContext(peers=peers, note=_NOTE, provenance=Provenance(source="SEC EDGAR"))
```
- [ ] Run → green; full suite. Commit `feat(peers): value-chain map + fetch_industry_context`.

---

### Task 3: Wire into build_dossier + mock

**Files:** `saturn/ingestion/dossier.py`; test `tests/ingestion/test_dossier.py` (or wherever build_dossier is tested).

- [ ] **Failing test:** with `build_dossier` monkeypatched so `fetch_industry_context` returns a canned `IndustryContext`, assert `dossier.industry_context is not None`; and a mock dossier (`build_dossier("MU", mock=True)`) has a non-None `industry_context`.
- [ ] **Implement:** import `fetch_industry_context`, `IndustryContext`. After the consensus block in `build_dossier`, add:
```python
    def _industry():
        return fetch_industry_context(ticker, ident.get("industry"))
    industry_ctx, gap = route_to_source("industry", _industry)
    if gap:
        gaps.append(gap)
```
and pass `industry_context=industry_ctx if isinstance(industry_ctx, IndustryContext) else None` into the `CompanyDossier(...)` constructor. In `_mock_dossier`, set a small canned `industry_context` (1-2 PeerSummary).
- [ ] Run → green; full suite. Commit `feat(dossier): fetch value-chain industry context via the soft-fail dispatcher`.

---

### Task 4: Context block + prompt + report subsection

**Files:** `saturn/workflows/equity_research.py`, `saturn/reports/markdown_report.py`; touched tests.

- [ ] **Failing tests:**
  - context: a dossier with an `industry_context` → `_company_context(dossier)` contains `"INDUSTRY / VALUE-CHAIN CONTEXT"` and a peer ticker.
  - render: a report whose `company.industry_context` has a peer → `render()` contains `"Value-Chain / Demand Context"` and the peer ticker; absent → not present.
- [ ] **Implement:**
  - `_company_context`, after the consensus block, add:
```python
    ic = dossier.industry_context
    if ic and ic.peers:
        lines.append("\nINDUSTRY / VALUE-CHAIN CONTEXT (peer as-reported proxies for demand/supply):")
        for p in ic.peers:
            bits = []
            if p.revenue_growth_yoy is not None:
                bits.append(f"rev growth {p.revenue_growth_yoy:+.0%} YoY")
            if p.capex is not None:
                bits.append(f"capex ${p.capex / 1e9:.1f}B")
            if p.capex_intensity is not None:
                bits.append(f"capex/rev {p.capex_intensity:.0%}")
            lines.append(f"- {p.ticker} [{p.role}]: {', '.join(bits) or 'n/a'} (source: SEC EDGAR)")
        lines.append(f"  NOTE: {ic.note}")
```
  - `ANALYSIS_SYSTEM`: append a clause: "Use the INDUSTRY / VALUE-CHAIN CONTEXT (peer revenue growth and hyperscaler capex) to triangulate whether the company's demand tailwind is corroborated and durable; treat it as a demand proxy, not unit or price data."
  - `markdown_report.render`, immediately AFTER the §3 Business Segments prose (`out += ["## 3. Business Segments", "", a.business_segments, ""]`), add:
```python
    ic = c.industry_context
    if ic and ic.peers:
        out += ["### Value-Chain / Demand Context", ""]
        out.append("| Peer | Role | Rev growth YoY | CapEx | CapEx/Rev |")
        out.append("| --- | --- | --- | --- | --- |")
        for p in ic.peers:
            rg = f"{p.revenue_growth_yoy:+.1%}" if p.revenue_growth_yoy is not None else "N/A"
            cx = f"${p.capex / 1e9:.1f}B" if p.capex is not None else "N/A"
            ci = f"{p.capex_intensity:.1%}" if p.capex_intensity is not None else "N/A"
            out.append(f"| {p.ticker} | {p.role} | {rg} | {cx} | {ci} |")
        out += ["", f"_{ic.note}_", ""]
```
  (`c` is `report.company`; this is a `###` subsection so NO section renumbering.)
- [ ] Run → green; full suite. Commit `feat(report): value-chain demand context in analyst context + §3 subsection`.

---

## Final verification (live)

`build_dossier('MU')` → `industry_context.peers` populated (NVDA/MSFT/… with rev-growth + capex). Regenerate MU; confirm the "Value-Chain / Demand Context" table under §3 and that the analyst references peer demand signals. Then finish the branch (PR to main).

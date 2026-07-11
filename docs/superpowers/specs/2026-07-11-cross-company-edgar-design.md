# Cross-Company EDGAR (Industry / Value-Chain Context) — Design

**Date:** 2026-07-11
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Give the analyst **demand-side / value-chain corroboration** for the target company by
pulling a curated set of related public companies' headline as-reported figures — the
biggest *attainable, deterministic* step toward industry-chain depth (chosen over paid
industry data / transcripts). For a memory name like MU: is the memory tailwind
corroborated by **Nvidia revenue growth** and **hyperscaler capex**?

## 2. Feasibility (confirmed)

Via our existing EDGAR pipeline: **US filers work** (NVDA rev +62.5% YoY; MSFT +18.3%,
capex-intensity 37.3%; AMAT +7.7%). **Foreign filers do NOT** — TSMC/ASML file 20-Fs under
the IFRS taxonomy, so our us-gaap concept map returns zero. So the value chain is
**US-filers only** (foundry/litho bottleneck out of scope), and the signals are
**revenue/capex proxies**, not GPU units / HBM content (those are estimate data).

## 3. Design

### Curated value-chain map (`saturn/ingestion/peers.py`)
```python
_AI_COMPUTE_CHAIN = [
    ("NVDA", "demand"), ("AMD", "demand"),
    ("MSFT", "demand"), ("GOOGL", "demand"), ("AMZN", "demand"), ("META", "demand"),
    ("AMAT", "supply"), ("LRCX", "supply"),
]
VALUE_CHAIN = {"semiconductor": _AI_COMPUTE_CHAIN}   # matched by substring on the target's industry
```
`_peers_for(industry)` returns the chain whose keyword is a substring of the (lowercased)
industry; empty when unmapped (graceful no-op). Extensible to other sectors later.

### Lightweight per-peer fetch
`_peer_summary(ticker, role)`: `ticker_to_cik` → `_fetch_companyfacts` → `_parse_companyfacts`
→ `compute_metrics`. NO filing sections / 8-Ks (cheap). Reduce to a `PeerSummary`:
`revenue_ttm`, `revenue_growth_yoy` (latest), `capex` (latest CapitalExpenditures fact),
`capex_intensity` (latest). Any peer that errors is skipped.

`fetch_industry_context(target_ticker, industry) -> IndustryContext`: iterate the chain
(self-excluded), collect summaries; raise `DataUnavailable` if none (→ dispatcher gap).

### Models (`saturn/models.py`)
```python
class PeerSummary(BaseModel):
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
`CompanyDossier.industry_context: IndustryContext | None = None`.

### Integration
- **`build_dossier`**: `route_to_source("industry", lambda: fetch_industry_context(ticker, ident.get("industry")))`; attach to the dossier (gap on failure). `_mock_dossier` gets a small canned `industry_context`.
- **Context** (`_company_context`): an `INDUSTRY / VALUE-CHAIN CONTEXT` block listing each
  peer's role + rev-growth + capex(+intensity), followed by the honest `note`.
- **Prompt** (`ANALYSIS_SYSTEM`): one clause — use the peer revenue-growth / hyperscaler
  capex to **triangulate** whether the demand tailwind is corroborated and durable; it is a
  proxy, not unit/price data.
- **Report**: a `### Value-Chain / Demand Context` **subsection appended under §3 Business
  Segments** (a `###`, so NO top-level renumbering) — a compact peer table + the note.
  Absent → nothing rendered.

## 4. Honest boundaries (in the report `note`)

"US-filer value-chain proxies (revenue/capex); excludes foreign filers (TSMC/ASML, IFRS)
and does not include GPU-unit or HBM-content estimates." So it's demand *corroboration*,
not a supply-demand model.

## 5. Cost

~5–7 extra companyfacts fetches per report, **cached** (per-source TTL). First run slower;
cached thereafter. Failures are soft (dispatcher gap), never crash the build.

## 6. Verification (live)

MU report gains a Value-Chain / Demand Context table (NVDA rev +~62%, MSFT capex-intensity
~37%, …) and the analyst triangulates the memory tailwind against it.

## 7. Scope

- **Create:** `saturn/ingestion/peers.py`, `tests/ingestion/test_peers.py`.
- **Modify:** `saturn/models.py` (2 models + dossier field), `saturn/ingestion/dossier.py`
  (wire + mock), `saturn/workflows/equity_research.py` (context + prompt),
  `saturn/reports/markdown_report.py` (§3 subsection), touched tests.

## 8. Out of scope

- Foreign filers (IFRS), GPU-unit / HBM-content / ASP estimates (paid/estimate data).
- Dynamic peer discovery; per-ticker maps beyond the semiconductor chain (add later).
- A full supply-demand model (a modeling layer on top of these signals).

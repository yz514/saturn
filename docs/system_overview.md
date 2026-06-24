# Saturn — System Overview (current state)

> What Saturn does today: its sources, how it computes, and what the analyze/debate
> passes actually enforce for the investment view. Reflects the codebase as of the
> derived-metrics + metric-fidelity + forward-metrics work. For the canonical metric
> formulas see [`docs/metrics.md`](metrics.md) (generated from the code catalog).

## 1. One-line summary

Saturn turns a ticker into a structured, source-cited equity research report. It
ingests authoritative data, computes ~56 provenance-tagged metrics deterministically
(49 trailing + 7 forward), then runs two grounded LLM passes (analyze → debate) that
must reason *only* from that data, and renders a markdown report with a "not
investment advice" disclaimer.

**Pipeline** (`saturn research <TICKER>`, CLI-first, local):

```text
ingest (build_dossier) -> analyze (LLM) -> debate (LLM) -> render (markdown)
```

`--mock` swaps only two boundaries (fixture ingestion + `MockLLMClient`); everything
else is identical, which is the path the offline test suite exercises.

---

## 2. Data sources

Everything is assembled into one `CompanyDossier`. A **soft-fail dispatcher**
(`route_to_source -> (result, gap)`) wraps each source: if one fails it is recorded
as a *gap* in the report rather than crashing the run. **Every datum carries a
`Provenance{source, source_url, as_of, retrieved_at}`** — the anti-hallucination
foundation: the model can only cite what is tagged.

| Source | Auth | What it provides | How |
|---|---|---|---|
| **yfinance** | none | `Quote`: price, market cap, currency; news headlines (often empty) | Python lib, `source="yfinance"` |
| **SEC EDGAR** | **no API key** — only a contact-email `User-Agent` (SEC fair-access) | (a) **companyfacts XBRL** -> ~28 canonical financial concepts (income statement, balance sheet, cash flow, per-share/shares), annual (10-K) + quarterly (10-Q); (b) **submissions feed** -> latest 10-K (Risk Factors + MD&A), latest 10-Q (MD&A), and **8-K material events** for the last ~12 months (high-value items carry body excerpts) | `data.sec.gov` JSON + filing HTML, parsed by pure functions |
| **FRED** | free API key | 16 curated macro series: Fed funds, 2Y/10Y/spread, CPI / core CPI / core PCE / PPI, real GDP, unemployment, nonfarm payrolls, M2, high-yield credit spread, VIX, WTI oil, trade-weighted dollar | `api.stlouisfed.org`, latest observation each |

### EDGAR parsing is the hard part, and it is hardened (verified live)

- **Concepts map through alias lists, merged per fiscal period** — a filer that
  migrates tags mid-history (e.g. net income `NetIncomeLoss -> ProfitLoss`, equity
  `-> ...IncludingNoncontrollingInterest`) still gets every recent year recovered.
- **Annual facts are keyed by the period *end date***, not companyfacts' filing-scoped
  `fy` field (which collapses multi-year comparatives into one year).
- **Quarterly facts** prefer the genuine ~3-month duration over the cumulative YTD;
  **balance-sheet "instant" values** are deduped by period-end; figures mistagged
  `fp=FY` are excluded by span.

> Verified: for AVGO, all 17 headline as-reported inputs matched raw SEC companyfacts
> **to the dollar**, and tied out to Broadcom's real filings (FY2024 revenue $51.574B,
> net income $5.895B).

---

## 3. How it computes

### 3a. As-reported facts (`FinancialFact`)
Exact SEC values, period-correct — faithful extraction with provenance, no transformation.

### 3b. Derived metrics — deterministic, ~49 (`DerivedMetric`, `source="Saturn (derived)"`)
Pure Python (`saturn/analytics/metrics.py`), **no LLM**. Each metric carries its
**formula + the exact input facts** it consumed, so any number is reproducible and
(eventually) Critic-verifiable. A declarative **`METRIC_CATALOG`
(`saturn/analytics/catalog.py`) is the single source of truth** for every metric's
name/format/formula/caveats, and it *generates* `docs/metrics.md`; a drift-guard test
plus a strict catalog<->compute coverage test make the code and the doc impossible to
desync.

Categories: **profitability** (gross/operating/net/EBITDA/FCF margin), **returns**
(ROE/ROA/ROIC/ROCE), **liquidity** (current/quick/cash), **leverage** (debt/equity,
net debt, net-debt/EBITDA, interest coverage), **efficiency** (asset/inventory
turnover, capex intensity, DSO), **cash** (FCF, FCF conversion), **growth**
(YoY/CAGR/QoQ), **per-share** (FCF/sh, BVPS), **TTM** (revenue/net income/EPS),
**valuation** (P/E, P/S, P/B, P/FCF, EV/EBITDA, EV/Sales, yields, payout), **quality
& capital return** (accruals, effective tax, buyback/total-shareholder yield,
share-count change).

**Correctness guards:** division-by-zero -> metric omitted (never fabricated);
negatives pass through (real signals like negative equity); **flow-vs-stock ratios are
annual-only** (a 3-month flow over a point-in-time stock would understate ~4x);
**per-share growth is skipped across split-like share changes**; a **recency window**
drops metrics older than the latest minus 5 years so a stale concept can't surface
ancient periods as "current."

> Verified: all **227** AVGO derived metrics recomputed exactly via an independent
> code path (0 mismatches).

### 3c. Forward metrics — reverse-DCF, 7 (`DerivedMetric`, `source="Saturn (model)"`)
A 2-stage reverse-DCF on **verified levered FCF** (`saturn/analytics/forward.py`),
derived *only* from price + as-reported FCF — **no scraped estimates**. Outputs:
`implied_fcf_growth` (the 10-yr FCF growth the price bakes in), `expectations_gap`
(implied vs trailing 3-yr FCF CAGR), `implied_return`, fair-value range per share
(low/mid/high over an 8/10/12% discount grid), `margin_of_safety`. Default assumptions
(10-yr horizon, 2.5% terminal growth, 25% growth cap) are recorded as `inputs`, and
the whole block is **skipped when FCF <= 0** (no fabrication for cash-burners). Tagged
`"Saturn (model)"` to flag they are assumption-dependent.

> Verified: all 7 AVGO forward metrics matched an independent closed-form DCF, and the
> solved growth/return satisfy `DCF == market_cap` to solver precision.

---

## 4. What `analyze` / `debate` enforce for the investment view

Both are **single LLM calls** fed the *same* context: a fully provenance-tagged
rendering of the dossier (`_company_context`) — company identity, quote, recent
fundamentals **with their sources**, a **DERIVED METRICS** block (each with its
formula + "Saturn derived"), a **FORWARD / EXPECTATIONS** block (the reverse-DCF,
marked "Saturn model"), 10-K/10-Q **MD&A excerpts**, **8-K material events**, the
**macro snapshot**, news, and an explicit **DATA GAPS** list.

### What is actually enforced

1. **Grounding (prompt-level).** The `analyze` system prompt: *"You are a rigorous
   buy-side equity research analyst. Base every statement only on the provided company
   data. Do not invent figures. Be concise and balanced."* The model only sees the
   tagged dossier — there is no open web.
2. **Mandatory balance (structural).** `debate` must produce a **bull thesis AND a
   bear thesis** ("the strongest honest case for each side"), then a **balanced final
   view** — you cannot get a one-sided take.
3. **Structured schema (validated).** Output is JSON validated by Pydantic —
   `AnalysisSections` (executive summary, company overview, business segments,
   financial snapshot, valuation discussion, key risks, **open questions**) and
   `DebateSections` (bull, bear, **final view**). Malformed/truncated JSON -> clean
   `LLMResponseError` exit (no half-baked report); non-string fields are coerced.
4. **Deterministic numbers in front of the reasoning.** Because the verified metrics —
   including the **expectations gap** and **margin of safety** — are in the context,
   the model reasons *from* computed figures rather than eyeballing raw statements. On
   the live AVGO run it recited them closely ("70.6x TTM P/E... ~42% FCF margin...
   -35.8% margin of safety").
5. **"Open questions" as a forced humility check.** `analyze` must list what data is
   missing / unresolved — surfacing gaps instead of papering over them.
6. **Disclaimer.** Every report ends with *"for research and educational purposes only
   and is not investment advice."*

### What is *not* yet enforced (be clear-eyed)

- There is **no automated Critic** verifying the LLM's prose claims against the
  provenance — that is the planned Phase 1 (a critic agent that checks each
  claim/number against the tagged data and flags hallucination). Today, grounding is
  enforced by *prompt discipline + structured schema + a clean grounded context*, not
  by post-hoc machine verification.
- The **`final_view` is a balanced narrative judgment, not a deterministic buy/sell
  signal or numeric score.** No rule maps margin-of-safety or expectations-gap to a
  rating; the model assigns a qualitative stance/confidence in prose.
- It is a **single pass per agent** — no multi-agent Planner/Industry/PM orchestration
  yet (also Phase 1), and no peer/relative comparison or consensus estimates (named
  follow-on slices).

**Honest framing:** Saturn enforces that the investment discussion is grounded in
authoritative, deterministically-computed, source-cited data, presented as a forced
bull/bear/synthesis with explicit gaps — but the *final judgment* is still a
disciplined LLM narrative, not a verified or rule-based decision. Closing that gap (the
Critic) is the next major phase.

---

## 5. Report structure

`reports/<TICKER>_<DATE>.md`: (1) Executive Summary, (2) Company Overview,
(3) Business Segments, (4) Recent Market Performance, (5) Financial Snapshot
(as-reported table, bounded), (6) **Key Metrics** (derived table + a **Forward /
Expectations** model sub-table), (7) Recent News & Catalysts, (8) Bull Thesis,
(9) Bear Thesis, (10) Key Risks, (11) Valuation Discussion, (12) Open Questions,
(13) Final View, (14) Macro Snapshot, (15) Material Events (8-K), (16) Sources,
(17 Data Gaps when present), then the disclaimer.

## 6. Run

```powershell
cd C:\Users\19454\Documents\saturn
.\.venv\Scripts\python.exe -m saturn.cli research <TICKER>          # live
.\.venv\Scripts\python.exe -m saturn.cli research <TICKER> --mock   # offline fixture
.\.venv\Scripts\python.exe -m saturn.cli doctor <TICKER>            # live dependency check
.\.venv\Scripts\python.exe -m saturn.cli metrics                    # print metric reference
```

`.env` holds `ANTHROPIC_API_KEY`, `SEC_USER_AGENT` (contact email), `FRED_API_KEY`.

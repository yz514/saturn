# Data-Ingestion Enrichment — Design Spec

**Date:** 2026-05-31
**Status:** Approved (design sign-off); ready for `writing-plans`.
**Author:** Saturn dev workflow (brainstorming skill).

## Motivation

Phase 0 ships a runnable `saturn research <TICKER>`, but the agents reason over a
thin `CompanyData` derived almost entirely from yfinance `.info`. Live runs
surfaced follow-up **F1 (hallucination / grounding)**: both Sonnet and Opus
asserted unverified specifics (partnership names, competitor chips) not present
in the fetched data, despite a "use only provided data" system prompt. A bigger
model did **not** fix this — Opus hallucinated *more* confidently. The fix is
architectural: a **thicker, sourced, provenance-tagged evidence base** the future
Critic/verification layer can check claims against.

This spec designs that evidence base: a reusable data-platform framework plus
**Slice 1** of sources (SEC EDGAR + FRED), keeping yfinance for live price only.
Later slices add FMP (Slice 2) and Finnhub news (Slice 3).

## Prior art reviewed

Code-read of two open-source multi-agent finance frameworks
(see `docs/roadmap.md` → Prior art; memory `saturn-prior-art`):

- **FinRobot** (`finrobot/data_source/*`): per-source utility classes with
  `@decorate_all_methods(init_*)` credential setup; **no canonical schema**
  (DataFrames / JSON / `langchain Document` / strings coexist); caches **SEC
  sections only**, no TTL; data flattened to **markdown in prompts** — provenance
  lost at the prompt boundary.
- **TradingAgents** (`tradingagents/dataflows/*`): **vendor-agnostic dispatcher**
  `interface.py::route_to_vendor()` with `VENDOR_METHODS` map + **fallback
  chains**; typed `NoMarketDataError` vs rate-limit errors; caches **OHLCV only**;
  centralizes `normalize_symbol()`; data flattened to **CSV/prose strings** —
  provenance again lost once the LLM reads it.

**Key takeaway:** both mature, popular repos *lose provenance at the moment data
becomes a string for the LLM* — which is precisely F1. Holding canonical
structured objects end-to-end and rendering **with inline provenance** only at the
prompt edge is Saturn's deliberate differentiator. Three patterns are adopted
from the prior art: the dispatcher/fallback, typed no-data-vs-error exceptions,
and centralized ID normalization.

## §1. Architecture & data flow

A data-platform layer under `saturn/ingestion/` assembles a rich,
provenance-tagged `CompanyDossier`, replacing the thin `CompanyData` the agents
get today.

```text
build_dossier(ticker, *, mock)
  └─ route_to_source(source, ...)            # dispatcher with soft-fail fallback
       ├─ prices  (yfinance)  -> Quote (price, market cap)              [keep]
       ├─ edgar   (SEC)       -> Fundamentals (as-reported, multi-yr)
       │                         + FilingSections (Risk / MD&A / Segments)
       └─ fred    (FRED)      -> MacroSnapshot (curated series)
            ↓ every datum wrapped in Provenance {source, url, as_of, retrieved_at}
            ↓ raw + normalized + canonical cached to data/cache/ (gitignored)
   CompanyDossier  →  analyze / debate  (rendered WITH inline provenance)
```

## §2. Canonical model

Vendor-neutral Pydantic types, every fact carrying lineage:

- `Provenance` = `{source, source_url, as_of, retrieved_at}`
- `FinancialFact` = `{concept, value, unit, fiscal_period, provenance}`
- `Fundamentals` = collections of `FinancialFact` with multi-year history
- `FilingSection` = `{name, excerpt, full_text_cache_ref, provenance}`
- `MacroSeries` / `MacroSnapshot` = curated FRED series with provenance
- `Quote` = `{price, market_cap, currency, provenance}`
- `CompanyDossier` = identity (ticker, CIK, name) + quote + fundamentals
  + filing_sections + macro + news + `generated_at`

**Decision:** a new `CompanyDossier` *contains* the quote/identity rather than
bloating Phase-0 `CompanyData`. `CompanyData` stays as the thin yfinance quote
piece; the dossier is the new rich envelope agents consume. Prior art confirms
the cost of *not* having a canonical schema: every downstream consumer must
handle multiple data shapes.

## §3. Slice-1 sources, keys & normalization

- **EDGAR** (no key; requires a `User-Agent` with contact email per SEC rules):
  `companyfacts` XBRL → as-reported fundamentals + multi-year history; latest
  10-K → Item 1A (Risk Factors), Item 7 (MD&A), segment notes.
- **FRED** (free API key → `.env` as `FRED_API_KEY`): curated default series —
  Fed funds, CPI, PPI, 10Y & 2Y yields, unemployment, M2.
- **yfinance**: keep for `Quote` only; **drop its derived fundamentals** in
  favor of EDGAR as-reported numbers.

### §3a. Centralized symbol/ID normalization

One `ingestion/identifiers.py` module owns all cross-source ID resolution:
**ticker → CIK** (via SEC `company_tickers.json`, cached) and the **FRED
series-ID** registry. Adapters never resolve IDs themselves. (Adopted from
TradingAgents' centralized `normalize_symbol()`, done once up front.)

### Filing-text handling (F1-relevant)

Extract named sections **deterministically**, store full text in cache, put a
length-bounded **excerpt** into the dossier. **No summarizer LLM in ingestion** —
ingestion never hallucinates; the agents do the reading. LLM summarization of
filings is a later add, not Slice 1.

## §4. Integration & report

- New `ingestion/dossier.py` orchestrates the adapters; `cli.py` calls
  `build_dossier` instead of `fetch_company_data`.
- Workflow `_company_context` renders the dossier **with inline provenance**,
  e.g. *"Revenue FY2025: $X (10-K filed 2026-02-21)"*. This is the line neither
  FinRobot nor TradingAgents holds — both flatten to plain strings and lose
  provenance, which is the F1 bug. Saturn keeps canonical objects all the way
  through and renders to text **with citations** only at the prompt edge.
- Report: minimal change in Slice 1 — add a historical-financials table (EDGAR)
  and a macro snapshot. Richer report architecture stays the user's to design
  later.

## §5. Dispatch, caching, config & errors

### §5a. Dispatcher + soft-fail fallback

A thin `route_to_source(source, ...)` over the adapters (adopted from
TradingAgents' `route_to_vendor()`). Each source fails **soft**: if EDGAR/FRED is
down, the dossier is built with what's available + a noted gap, never a hard
crash (price-only still yields a report).

### §5b. Typed exception hierarchy

Replace the single `IngestionError` with:

- `DataUnavailable` — source reachable but datum genuinely absent (e.g. no CIK).
- `SourceFailure` — network / rate-limit / transport error.
- both subclass `IngestionError` (back-compat).

This lets the dispatcher distinguish "nothing here" from "try again / fall back,"
so agents never hallucinate around an empty string. (TradingAgents'
`NoMarketDataError` vs rate-limit split proved this matters.)

### Caching

`data/cache/<source>/<ticker>_<date>.json` (gitignored); same-day reuse;
**per-source TTL** (price ~1 day, FRED ~1 day, EDGAR ~30 days); raw → normalized
→ canonical layers (data-lake seed). Both prior-art repos cached only one source
each, with no TTL — we cache all three with TTL.

### Config

New `FRED_API_KEY`, `SEC_USER_AGENT` (default the developer's contact email),
documented in `.env.example`. `.env` stays gitignored; never commit real keys.

## §6. Testing (offline guarantee preserved)

- Adapter unit tests parse **recorded sample JSON fixtures** (committed) — no
  live EDGAR/FRED calls.
- Dispatcher tests assert fallback + the `DataUnavailable` vs `SourceFailure`
  branching (inject a failing adapter; confirm soft-fail + noted gap).
- A rich **mock dossier fixture** powers `--mock` and the CLI test.
- The autouse `.env`-neutralizing fixture extends to the new keys
  (`FRED_API_KEY`, `SEC_USER_AGENT`).

## §7. Scope / non-goals

**In scope:** canonical model + provenance + cache + dispatcher/fallback + typed
errors + ID normalization + EDGAR adapter + FRED adapter + integration into the
existing analyze/debate pipeline and report.

**Deferred:** FMP adapter (Slice 2); Finnhub news (Slice 3); full-document
RAG/retrieval (Phase 3); LLM summarization of filings; the Phase-1 specialized
agents themselves (the Critic that consumes this evidence base).

## Success criteria

- `saturn research <TICKER>` produces a report whose financial figures are
  EDGAR as-reported, each carrying provenance the renderer surfaces inline.
- `--mock` and the full test suite still run fully offline, no network, no keys.
- A source outage (EDGAR or FRED) degrades gracefully to a noted gap, not a crash.
- The canonical `CompanyDossier` is the single typed contract the analyze/debate
  stages (and future Phase-1 agents) consume.

## Next step

Invoke `writing-plans` to decompose this into bite-sized TDD tasks
(framework → identifiers → EDGAR adapter → FRED adapter → dispatcher → dossier
orchestration → pipeline/report integration → mock fixture), then execute via
subagent-driven development.

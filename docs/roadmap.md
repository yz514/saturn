# Saturn Roadmap

## Phase 0 — Foundation & MVP ✅ complete (PR #1, 2026-05-26)
Local, human-triggered `saturn research <TICKER>` producing a 13-section
markdown report. Sequential pipeline: ingest → analyze → debate → render.
Real yfinance data + LLM, with an offline `--mock` fallback.
See `docs/PROGRESS.md` for the completion record and open follow-ups.

## Phase 1 — Multi-Agent Research Workflow (next)
Split analyze/debate into specialized agents and adopt explicit graph
orchestration (LangGraph). Planned agent roster (deferred from Phase 0):
Planner, Research, Financial Analyst, Macro, Industry, Bull, Bear, Critic,
PM/Synthesis, Report Writer.

## Phase 2 — Data Platform Layer
Structured ingestion + storage: prices, SEC filings, transcripts, news; a local
storage layer separating raw / processed / generated outputs; metadata tracking.

> **Partially pulled forward (started 2026-05-31).** The data-enrichment slice is
> being built ahead of the full Phase 1 agent roster because it directly addresses
> follow-up F1 (hallucination/grounding): the future Critic needs a thick, sourced
> evidence base to verify claims against. Slices:
> - **Slice 1 — framework ✅ merged (PR #3, 2026-06-06).** Vendor-neutral canonical
>   `CompanyDossier` with provenance on every datum, soft-fail dispatcher + typed
>   errors, per-source TTL cache, real yfinance `Quote`, dossier orchestration with
>   EDGAR/FRED as injectable gap-seams, and inline-provenance rendering. See
>   `docs/PROGRESS.md`.
> - **Slice 1 — real adapters (next).** SEC EDGAR (companyfacts XBRL + targeted
>   10-K sections) and FRED (macro) behind the seams, plus `identifiers.py`
>   (ticker→CIK, FRED series). The genuinely-parallel EDGAR ‖ FRED pair.
> - **Slice 2 — FMP** (ratios/estimates/transcripts/segments) and **Slice 3 —
>   Finnhub** (news) follow.
>
> Spec: `docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md`;
> framework plan: `docs/superpowers/plans/2026-06-06-enrichment-framework.md`.

## Phase 3 — Persistent Memory
Company-level memory, thesis history, prior reports, vector search and a
retrieval layer so re-researching a ticker references prior conclusions.

## Phase 4 — Long-Running Workflows
Watchlist, scheduled jobs, retry/checkpoint logic, event-driven research.

## Phase 5 — Observability & Evaluation
Agent execution logs, prompt/version tracking, output quality + source-coverage
checks, hallucination-risk checks, cost/token tracking.

> **Backlog — concept-aware grounding for the Critic.** The Critic's numeric
> backstop is magnitude-only: a wrong figure can be dropped when it collides with an
> unrelated datum of the same size (e.g. "$2B revenue" grounding against a $2B OCF/D&A
> line). Make grounding tie a claim's number to its *subject concept* and match the
> right fact. Spec: `docs/superpowers/specs/2026-07-11-concept-aware-grounding-backlog.md`.

## Phase 6 — Productization
Web dashboard, search/watchlist UI, company pages, memo archive, exports,
notifications. Not needed yet.

## Prior art / references
Open-source and published multi-agent finance frameworks that implement the
analyst → bull/bear-debate → risk/critic → report architecture Saturn targets
in Phase 1. Worth reading before finalizing our own multi-agent + data-ingestion
design (esp. their ingestion layers):

- **FinRobot** — github.com/AI4Finance-Foundation/FinRobot (arXiv 2411.08804).
  Closest match: generates per-ticker equity research reports. Report-native.
- **TradingAgents** — github.com/TauricResearch/TradingAgents (Apache-2.0,
  arXiv 2412.20138). Best bull/bear structured-debate blueprint; output is
  buy/sell/hold rather than reports.
- **virattt/ai-hedge-fund** — github.com/virattt/ai-hedge-fund (MIT). Investor-
  persona agents (Buffett/Graham/Munger/Burry/Wood) on LangGraph; educational.
- **BlackRock AlphaAgents** — arXiv 2508.11152 (paper). Round-robin debate-to-
  consensus among Fundamental/Sentiment/Valuation agents; validates our planned
  Critic/consensus layer (targets follow-up F1).

Note: the viral "Serenity" AI-investor (46X) is a misnomer — a pseudonymous
*human* small-cap stock-picker with no code/repo/paper, conflated with a crypto
LLM-trading benchmark. Not a technical reference; only the domain idea
("chokepoint" supply-chain-bottleneck small-caps) is interesting for later.

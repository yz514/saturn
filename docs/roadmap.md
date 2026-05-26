# Saturn Roadmap

## Phase 0 — Foundation & MVP (current)
Local, human-triggered `saturn research <TICKER>` producing a 13-section
markdown report. Sequential pipeline: ingest → analyze → debate → render.
Real yfinance data + LLM, with an offline `--mock` fallback.

## Phase 1 — Multi-Agent Research Workflow
Split analyze/debate into specialized agents and adopt explicit graph
orchestration (LangGraph). Planned agent roster (deferred from Phase 0):
Planner, Research, Financial Analyst, Macro, Industry, Bull, Bear, Critic,
PM/Synthesis, Report Writer.

## Phase 2 — Data Platform Layer
Structured ingestion + storage: prices, SEC filings, transcripts, news; a local
storage layer separating raw / processed / generated outputs; metadata tracking.

## Phase 3 — Persistent Memory
Company-level memory, thesis history, prior reports, vector search and a
retrieval layer so re-researching a ticker references prior conclusions.

## Phase 4 — Long-Running Workflows
Watchlist, scheduled jobs, retry/checkpoint logic, event-driven research.

## Phase 5 — Observability & Evaluation
Agent execution logs, prompt/version tracking, output quality + source-coverage
checks, hallucination-risk checks, cost/token tracking.

## Phase 6 — Productization
Web dashboard, search/watchlist UI, company pages, memo archive, exports,
notifications. Not needed yet.

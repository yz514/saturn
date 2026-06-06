# System Overview (Phase 0)

## Pipeline

```text
saturn research <TICKER> [--mock]
  ingestion.build_dossier  -> CompanyDossier
  workflows.run
    analyze (LLM call 1) -> AnalysisSections
    debate  (LLM call 2) -> DebateSections
  reports.render -> markdown
  write reports/<TICKER>_<DATE>.md
```

`--mock` swaps two boundaries only: ingestion returns a fixture, and the
orchestrator gets a `MockLLMClient`. Everything else is identical, which is also
the path the offline test suite exercises.

## Key interfaces

- `LLMClient` Protocol — `AnthropicClient` (default, prompt-cached) and
  `MockLLMClient`. Adding OpenAI/Gemini later is one new class.
- `CompanyData` — typed ingestion output, source-agnostic.
- `workflows.run(company, llm, ...)` — dependency-injected, fully testable.
- `reports.render(report)` — pure function.

## Ingestion enrichment (in progress — `CompanyDossier`)

Phase 0's thin `CompanyData` (mostly yfinance `.info`) is being replaced as the
agent-facing contract by a rich, provenance-tagged `CompanyDossier`. Design spec:
`docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md`.

```text
build_dossier(ticker, *, mock)
  route_to_source(source, ...)               # dispatcher, soft-fail fallback
    prices (yfinance) -> Quote               # price / market cap only
    edgar  (SEC)      -> Fundamentals (as-reported, multi-yr) + FilingSections
    fred   (FRED)     -> MacroSnapshot
      every datum wrapped in Provenance {source, url, as_of, retrieved_at}
      raw + normalized + canonical cached to data/cache/ (gitignored)
  -> CompanyDossier  ->  analyze / debate (rendered WITH inline provenance)
```

Key principles: a **vendor-neutral canonical model** (every fact is a
`FinancialFact`/`FilingSection`/… carrying `Provenance`); **structure held
end-to-end**, rendered to text *with citations* only at the prompt edge (this is
the F1 hallucination fix — prior-art frameworks lose provenance when they flatten
to strings); a **dispatcher with typed `DataUnavailable` vs `SourceFailure`
errors** so a source outage degrades to a noted gap, not a crash; centralized
ticker→CIK / FRED series-ID resolution in `ingestion/identifiers.py`; per-source
TTL caching. Slice 1 = EDGAR + FRED; FMP (Slice 2) and Finnhub (Slice 3) follow.

## Future (Phase 1+)

The analyze/debate steps become a graph of specialized agents (LangGraph), with a
Critic that verifies claims against the dossier's provenance. The Phase 0
interfaces are designed so this is additive, not a rewrite. See `docs/roadmap.md`.

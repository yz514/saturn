# System Overview (Phase 0)

## Pipeline

```text
saturn research <TICKER> [--mock]
  ingestion.fetch_company_data  -> CompanyData
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

## Future (Phase 1+)

The analyze/debate steps become a graph of specialized agents (LangGraph). The
Phase 0 interfaces are designed so this is additive, not a rewrite. See
`docs/roadmap.md`.

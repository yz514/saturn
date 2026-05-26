# Saturn Progress Log

A running record of what's been built, how it was verified, and open follow-ups.
For the forward-looking plan see `docs/roadmap.md`.

---

## Phase 0 — Foundation & MVP ✅ COMPLETE (2026-05-26)

Merged via **PR #1** (`yz514/saturn#1`, branch `phase0-foundation` → `main`, merge commit `eea273f`).

### Delivered
- CLI: `saturn research <TICKER>` → 13-section markdown report at `reports/<TICKER>_<DATE>.md` with mandatory "not investment advice" disclaimer.
- Sequential pipeline: `ingest → analyze (LLM) → debate (LLM) → render`.
- Provider-agnostic `LLMClient` Protocol: `AnthropicClient` (default, prompt-cached) + deterministic `MockLLMClient`.
- `--mock` path: fully offline (sample fixture + mock client), no network, no API key — also what the test suite uses.
- yfinance ingestion → typed `CompanyData`; Pydantic models throughout.
- Foundation docs: `CLAUDE.md`, `README.md`, `docs/vision.md`, `docs/roadmap.md`, `docs/engineering_principles.md`, `architecture/system_overview.md`, design spec + implementation plan under `docs/superpowers/`, and a committed example (`examples/nvda_research_report.md`).

### How it was built
Spec → plan → subagent-per-task TDD execution, with per-task spec review, dedicated fresh-context reviewers on the substantive modules (workflow, CLI), and a final whole-branch review (verdict: ready to merge). Two review findings actioned: the `model_used`/`AnthropicClient` default footgun, and an `.env` offline-leak in tests (fix proven by planting a fake `.env`).

### Verification
- `python -m pytest` → **24 passed**, fully offline.
- `saturn research NVDA --mock` → 13-section report, offline, no key.
- Live `saturn research NVDA` (Sonnet) → real yfinance data + 2 Anthropic calls, report written.
- Live `saturn research NVDA --model claude-opus-4-7` (Opus) → confirmed; sharper bear/risk analysis.

---

## Open follow-ups (discovered during live runs)

| # | Item | Severity | Notes |
|---|------|----------|-------|
| F1 | **Hallucination / grounding** | High | Both Sonnet and Opus asserted unverified specifics (e.g. partnership names, competitor chip products) not present in the fetched yfinance data, despite a "use only provided data" system prompt. Opus did so *more* confidently. Not fixable by a bigger model — needs a **Critic/verification layer (Phase 1)** + stricter provided-data discipline + source-coverage checks (Phase 5). |
| F2 | **News relevance** | Medium | yfinance `.news` returns general-market items (building materials, cannabis) not specific to the ticker. Needs relevance filtering during ingestion. |
| F3 | **Report filename clobbering** | Medium | Filename is per-day (`<TICKER>_<DATE>.md`), so multiple runs the same day (e.g. Sonnet then Opus) overwrite each other. Include model and/or timestamp in the filename. |
| F4 | **Metrics table formatting** | Low | §5 renders raw floats (e.g. `46335873024.0`, `0.852`). Humanize as `$`/`%`/magnitudes; reconcile `$215` (§4) vs `$215.33` (§1). |
| F5 | **`_extract_json` trailing-content edge case** | Low | Closing-fence stripping assumes the fence is the last line; malformed LLM output with trailing prose would break parsing. Cannot be triggered by the mock or a well-behaved LLM today. |

---

## Next

**Phase 1 — Multi-Agent Research Workflow.** Split analyze/debate into specialized
agents (Planner, Research, Financial Analyst, Macro, Industry, Bull, Bear,
**Critic**, PM/Synthesis, Report Writer) on explicit graph orchestration
(LangGraph). The Critic agent directly targets follow-up **F1**.

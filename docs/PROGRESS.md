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

## Data-Enrichment Slice 1 — Framework ✅ COMPLETE (2026-06-06)

Merged via **PR #3** (`yz514/saturn#3`, branch `data-enrichment-slice1` → `main`, merge commit `e258166`).

The framework spine for thicker, sourced data — built ahead of the full Phase-1
agent roster because it directly attacks **F1** (the future Critic needs a
provenance-tagged evidence base). Design spec
`docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md`, plan
`docs/superpowers/plans/2026-06-06-enrichment-framework.md`.

### Delivered
- **Canonical model** (`saturn/models.py`): `Provenance` on every datum +
  `Quote`, `FinancialFact`, `Fundamentals`, `FilingSection`, `MacroSeries`,
  `MacroSnapshot`, `SourceGap`, `CompanyDossier`. `ResearchReport.company` is now
  a `CompanyDossier` (the new agent-facing contract; thin `CompanyData` retained
  transitionally).
- **Typed errors** (`saturn/ingestion/errors.py`): `DataUnavailable` vs
  `SourceFailure` under `IngestionError`.
- **Soft-fail dispatcher** (`saturn/ingestion/dispatch.py`): `route_to_source`
  → `(result, gap)`; one flaky source never crashes the run.
- **Per-source TTL disk cache** (`saturn/ingestion/cache.py`), gitignored under
  `data/cache/` (deterministic — dates injected, not read from the clock).
- **Quote adapter** (`saturn/ingestion/prices.py::fetch_quote`): real yfinance →
  canonical `Quote`.
- **Dossier orchestration** (`saturn/ingestion/dossier.py::build_dossier`): wires
  the quote; EDGAR/FRED are injectable seams (`edgar_fn`/`fred_fn`) that degrade
  to recorded gaps until their adapters land.
- **Pipeline / report / CLI** consume the dossier; `_company_context` renders
  **inline provenance** (the F1 fix — citable evidence instead of a JSON dump);
  the report gains a source-tagged financials table, a Macro Snapshot section,
  and a conditional Data Gaps section.
- **Config**: `FRED_API_KEY`, `SEC_USER_AGENT` (+ `.env.example`, offline test
  guard extended).

Three patterns adopted from prior-art code review (FinRobot, TradingAgents): the
dispatcher/fallback, typed no-data-vs-error exceptions, and (deferred) centralized
ID normalization. See `docs/roadmap.md` → Prior art.

### How it was built
Brainstorm → spec → plan → **subagent-driven TDD**: 10 bite-sized tasks, each a
fresh-context implementer followed by a two-stage review (spec compliance, then
code quality) with fix loops, plus a final whole-branch opus review (verdict:
ready to merge, zero critical/important). Reviews caught and fixed real issues:
an untested cache "freshest-wins" branch, a misleading `Company data (JSON):`
prompt label (now plain text), news items missing provenance, and an uncovered
data-gaps render path.

### Verification
- `python -m pytest` → **52 passed**, fully offline.
- `saturn research NVDA --mock` → enriched report with a source-tagged
  financials table (`Revenues FY2024 … SEC EDGAR (mock)`), a Macro Snapshot, and
  the disclaimer.
- Real-path gap check: missing EDGAR/FRED degrade to `gaps=['edgar','fred']`,
  never a crash; quote still populates.

### Deferred to follow-on plans (by design)
- Real **SEC EDGAR** adapter (companyfacts XBRL + 10-K Risk Factors/MD&A/Segments)
  and **FRED** adapter — implement the `edgar_fn`/`fred_fn` seams — plus
  `saturn/ingestion/identifiers.py` (ticker→CIK, FRED series registry). These are
  the next, genuinely-parallel pair of plans.
- **Cleanup once adapters land:** delete the now-unused
  `fetch_company_data`/`_mock_company`/`_extract_news` and `CompanyData` (and
  repoint/remove `tests/test_ingestion.py`); wire `read_cache`/`write_cache` into
  the adapters with the spec TTLs (price ~1d, FRED ~1d, EDGAR ~30d).

---

## Open follow-ups (discovered during live runs)

| # | Item | Severity | Notes |
|---|------|----------|-------|
| F1 | **Hallucination / grounding** | High (in progress) | *Partially addressed by Enrichment Slice 1:* the agent context is now provenance-tagged evidence (citable source per datum) rather than a raw JSON dump, and the data base is being thickened with as-reported sources. Full fix still needs a **Critic/verification layer (Phase 1)** that checks claims against that provenance + source-coverage checks (Phase 5). |
| F2 | **News relevance** | Medium | yfinance `.news` returns general-market items (building materials, cannabis) not specific to the ticker. Needs relevance filtering during ingestion. |
| F3 | **Report filename clobbering** | Medium | Filename is per-day (`<TICKER>_<DATE>.md`), so multiple runs the same day (e.g. Sonnet then Opus) overwrite each other. Include model and/or timestamp in the filename. |
| F4 | **Metrics table formatting** | Low (partly addressed) | Enrichment Slice 1's report humanizes USD values in the financials table via `_fmt_money`; non-USD values (e.g. share counts, ratios) still render as raw floats and want unit-aware formatting. Original §1/§4 price reconciliation still open. |
| F5 | **`_extract_json` trailing-content edge case** | Low | Closing-fence stripping assumes the fence is the last line; malformed LLM output with trailing prose would break parsing. Cannot be triggered by the mock or a well-behaved LLM today. |

---

## Next

**Enrichment Slice 1 — real adapters (the EDGAR ‖ FRED parallel pair).** Write and
execute two short plans that implement the `edgar_fn`/`fred_fn` seams built in the
framework, plus `saturn/ingestion/identifiers.py`:
- **SEC EDGAR** — companyfacts XBRL as-reported fundamentals (multi-year) +
  targeted 10-K sections (Risk Factors / MD&A / Segments), cached.
- **FRED** — curated macro series (Fed funds, CPI, PPI, 10Y/2Y, unemployment, M2).
These turn the mock dossier into real, sourced data and let us run a live
enriched report.

**Then Phase 1 — Multi-Agent Research Workflow.** Split analyze/debate into
specialized agents (Planner, Research, Financial Analyst, Macro, Industry, Bull,
Bear, **Critic**, PM/Synthesis, Report Writer) on explicit graph orchestration
(LangGraph). The Critic agent verifies claims against the dossier's provenance —
directly closing follow-up **F1**.

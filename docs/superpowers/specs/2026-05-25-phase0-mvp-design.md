# Saturn — Phase 0 MVP Design

**Date:** 2026-05-25
**Status:** Approved (design) — pending spec review
**Scope:** Phase 0 only (foundation + runnable `saturn research NVDA`)

---

## 1. Goal

Deliver the first runnable milestone:

```bash
saturn research NVDA          # real yfinance data + LLM analysis
saturn research NVDA --mock   # fully offline, no API key needed
```

Each run produces a structured markdown equity research report at
`reports/<TICKER>_<YYYY-MM-DD>.md` containing the 13 sections defined in
`CLAUDE.md`, ending with a "research/educational only, not investment advice"
disclaimer.

Phase 0 is **not** autonomous and **not** multi-agent. It is a clean,
sequential, well-bounded pipeline whose step boundaries map 1:1 onto future
LangGraph nodes (Phase 1+).

---

## 2. Scope

### In scope (Phase 0)
- Python package `saturn` installed as an editable package exposing a `saturn` console command.
- One CLI command: `research <TICKER>` with `--mock` and `--model` flags.
- Data ingestion from `yfinance` (price, company profile, key financials, recent news if available), returning a typed `CompanyData`.
- A provider-agnostic `LLMClient` interface with an `AnthropicClient` (default) and a deterministic `MockLLMClient`.
- A two-call analysis pipeline (`analyze`, `debate`) producing the report's reasoned sections.
- Markdown report rendering with all 13 sections + disclaimer + sources.
- Foundation docs: `vision.md`, `roadmap.md`, `engineering_principles.md`, `architecture/system_overview.md`.
- Offline test suite (no network, no API key) using mock data + `MockLLMClient`.
- A committed example report (`examples/nvda_research_report.md`) generated via `--mock`.

### Out of scope (deferred to later phases)
- Multiple specialized agents as separate modules (Planner, Macro, Industry, separate Bull/Bear, Critic, PM). Phase 0 collapses analysis into two LLM calls. Empty stub files are intentionally **not** created; the roadmap documents them.
- LangGraph / Temporal / Airflow / Kafka / Celery orchestration.
- Persistent / vector memory (Phase 3). No SQLite, Postgres, or Qdrant yet.
- Watchlist, scheduling, long-running jobs (Phase 4).
- Observability/eval beyond structured logging (Phase 5).
- Any web UI, notifications, or productization (Phase 6).
- Real-money trading, anything in `CLAUDE.md` Non-Goals.

---

## 3. Architecture & Data Flow

Single command, sequential pipeline:

```
saturn research NVDA [--mock]
        │
        ▼
  ingestion.prices.fetch_company_data(ticker, mock)  ──► CompanyData
        │
        ▼
  workflows.equity_research.run(company, llm)
        ├─ analyze(company, llm)  [LLM call 1] ─► AnalysisSections
        │      (executive summary, overview, segments, financial
        │       commentary, valuation discussion, risks, open questions)
        ├─ debate(company, llm)   [LLM call 2] ─► DebateSections
        │      (bull thesis, bear thesis, balanced final view)
        ▼
  ResearchReport  (CompanyData + AnalysisSections + DebateSections + meta)
        │
        ▼
  reports.markdown_report.render(report)  ──► markdown string
        │
        ▼
  write to reports/<TICKER>_<DATE>.md
```

**`--mock` behavior:** swaps two boundaries only —
1. ingestion returns a committed sample `CompanyData` fixture instead of calling yfinance;
2. the orchestrator is handed a `MockLLMClient` instead of `AnthropicClient`.

Everything else (orchestration, rendering, file write) is identical. This is
also exactly the path the test suite exercises, guaranteeing tests never touch
the network or require an API key.

---

## 4. Components & Interfaces

### 4.1 `saturn/config.py`
Loads settings from environment / `.env` via `pydantic-settings`.

```python
class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    default_model: str = "claude-sonnet-4-6"   # dev default; opus for quality runs
    reports_dir: Path = Path("reports")
    log_level: str = "INFO"
```

- No secrets hardcoded. Reads `ANTHROPIC_API_KEY` from `.env` (gitignored).

### 4.2 `saturn/llm/base.py` — `LLMClient`
Provider-agnostic interface (the chosen abstraction point).

```python
class LLMClient(Protocol):
    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str: ...
```

- `AnthropicClient(LLMClient)` (`anthropic_client.py`): wraps the Anthropic SDK,
  uses **prompt caching** on the system prompt, default model from `Settings`.
- `MockLLMClient(LLMClient)` (`mock_client.py`): deterministic. Returns fixed,
  clearly-labeled placeholder text keyed by a tag in the prompt (so `analyze`
  and `debate` get distinguishable canned output). No randomness — tests assert
  on it.

Swapping in OpenAI/Gemini later = one new class implementing the Protocol.

### 4.3 `saturn/ingestion/prices.py`
```python
def fetch_company_data(ticker: str, *, mock: bool = False) -> CompanyData: ...
```
- Real path: `yfinance` for profile, price/market cap, key financials, and
  recent news when available.
- Mock path: loads a committed NVDA-shaped fixture.
- Raises a clear, typed error (`IngestionError`) on unknown ticker / network
  failure, with a message suggesting `--mock`.

### 4.4 `saturn/models.py` (Pydantic)
- `CompanyData` — ticker, name, sector/industry, business summary, segments,
  price, market cap, selected financial metrics, recent news items, `as_of` date.
- `AnalysisSections` — the reasoned sections from call 1.
- `DebateSections` — bull, bear, final view.
- `ResearchReport` — composes the above + metadata (ticker, generated_at,
  model_used, mock: bool, sources: list).

### 4.5 `saturn/workflows/equity_research.py`
```python
def run(company: CompanyData, llm: LLMClient, *, model: str | None = None) -> ResearchReport: ...
```
- Orchestrates `analyze` then `debate`, assembles `ResearchReport`.
- Takes the `LLMClient` and `CompanyData` as arguments (dependency injection) →
  fully unit-testable with mocks.

### 4.6 `saturn/reports/markdown_report.py`
```python
def render(report: ResearchReport) -> str: ...
```
- Pure function. Emits the 13 sections in fixed order, a Sources section, the
  generated-at/model/mock metadata line, and the disclaimer footer.

### 4.7 `saturn/cli.py` (Typer)
```bash
saturn research NVDA [--mock] [--model claude-opus-...]
```
- Wires config → ingestion → workflow → render → file write.
- Prints the output path. On `--mock`, prints a clear "MOCK MODE" banner.
- Chosen Typer over argparse because the command surface will grow
  (`ingest`, `watchlist`, `memory`) per `CLAUDE.md`.

### 4.8 `saturn/utils/logging.py`
- Structured logging setup (stdlib `logging`, level from `Settings`). One line
  per pipeline step (ingest / analyze / debate / render / write).

---

## 5. Error Handling

| Failure | Behavior |
|---|---|
| Unknown ticker / yfinance network error | `IngestionError` with message: "Could not fetch data for <T>. Check the ticker or run with --mock." Non-zero exit. |
| Missing `ANTHROPIC_API_KEY` (non-mock run) | Clear message pointing at `.env.example`; suggest `--mock`. Non-zero exit. No partial report written. |
| LLM call failure | Surface the error; do **not** write a half-complete report. |
| `--mock` | Never requires network or key; always succeeds for a valid ticker string. |

---

## 6. Testing Strategy

All tests run offline (no network, no API key), using `MockLLMClient` + the mock `CompanyData` fixture.

- `test_markdown_report.py` — `render()` produces all 13 section headers, the
  disclaimer, and the sources section for a known `ResearchReport`.
- `test_equity_research_workflow.py` — `run()` with `MockLLMClient` + mock
  company yields a `ResearchReport` whose sections are populated.
- `test_cli.py` — `saturn research NVDA --mock` exits 0 and writes a file
  matching `reports/NVDA_*.md` (uses a tmp reports dir).

Run via `pytest`. CI is out of scope for Phase 0 (noted in roadmap).

---

## 7. Repository Layout (Phase 0)

```
saturn/
├── README.md                 # expanded: what/why, quickstart, status
├── CLAUDE.md                 # project context (already authored)
├── pyproject.toml            # package + deps + `saturn` console script
├── .env.example              # ANTHROPIC_API_KEY=
├── .gitignore                # python, .env, reports/ (keep .gitkeep)
│
├── docs/
│   ├── vision.md
│   ├── roadmap.md            # the 6 phases + deferred agent roster
│   ├── engineering_principles.md
│   └── superpowers/specs/2026-05-25-phase0-mvp-design.md   # this doc
│
├── architecture/
│   └── system_overview.md    # Phase 0 pipeline + future agent-graph sketch
│
├── saturn/
│   ├── __init__.py
│   ├── config.py
│   ├── cli.py
│   ├── models.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── anthropic_client.py
│   │   └── mock_client.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   └── prices.py
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── equity_research.py
│   ├── reports/
│   │   ├── __init__.py
│   │   └── markdown_report.py
│   └── utils/
│       ├── __init__.py
│       └── logging.py
│
├── tests/
│   ├── test_cli.py
│   ├── test_markdown_report.py
│   └── test_equity_research_workflow.py
│
├── examples/
│   └── nvda_research_report.md     # committed --mock output
│
└── reports/
    └── .gitkeep                    # runtime output dir (contents gitignored)
```

Empty agent stubs (`planner.py`, `macro.py`, `critic.py`, `pm.py`, etc.) are
**not** created in Phase 0 — they are described in `docs/roadmap.md` and added
in Phase 1 when they do real work.

---

## 8. Dependencies (Phase 0)

Runtime: `yfinance`, `anthropic`, `typer`, `pydantic`, `pydantic-settings`.
Dev: `pytest`.

No orchestration framework, no DB, no vector store in Phase 0.

---

## 9. Constraints

- Reports are research/educational only — not investment advice. The
  disclaimer footer is mandatory on every generated report.
- Never commit API keys; `.env` is gitignored, `.env.example` is the template.
- Equities/macro/finance focus; no Web3 as a core direction.

---

## 10. Success Criteria

1. `pip install -e .` (or `uv` equivalent) exposes the `saturn` command.
2. `saturn research NVDA --mock` writes a readable `reports/NVDA_2026-05-25.md`
   with all 13 sections + disclaimer, offline, with no API key.
3. `saturn research NVDA` (with key set) does the same using real yfinance data
   and live LLM analysis.
4. `pytest` passes offline.
5. `examples/nvda_research_report.md` is committed as a reference of the output.

---

## 11. Next Phase Pointer

Phase 1 splits `analyze`/`debate` into specialized agents (Planner, Research,
Financial Analyst, Macro, Industry, separate Bull/Bear, Critic, PM, Report
Writer) and introduces explicit graph orchestration (LangGraph). The Phase 0
interfaces (`LLMClient`, `CompanyData`, injected orchestrator) are designed so
this migration is additive, not a rewrite.

# Saturn Phase 0 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable `saturn research NVDA` command that produces a 13-section markdown equity research report from real yfinance data + LLM analysis, with a fully-offline `--mock` fallback.

**Architecture:** A sequential Python pipeline — `ingest -> analyze (LLM) -> debate (LLM) -> render -> write file`. Each step is a small module behind a clean interface, with dependency injection (LLM client + data source passed in) so the workflow is unit-testable offline. A provider-agnostic `LLMClient` Protocol has an `AnthropicClient` (default) and a deterministic `MockLLMClient`.

**Tech Stack:** Python 3.11+, Pydantic v2 + pydantic-settings (models/config), Typer (CLI), yfinance (data), anthropic SDK (LLM), pytest (tests). No orchestration framework, DB, or vector store in Phase 0.

**Spec:** `docs/superpowers/specs/2026-05-25-phase0-mvp-design.md`

---

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, deps, `saturn` console script |
| `.gitignore` / `.env.example` | Ignore `.env`/`reports/`; document env vars |
| `saturn/config.py` | `Settings` (env/.env) + `get_settings()` + `ConfigError` |
| `saturn/models.py` | Pydantic models: `NewsItem`, `CompanyData`, `AnalysisSections`, `DebateSections`, `ResearchReport` |
| `saturn/llm/base.py` | `LLMClient` Protocol |
| `saturn/llm/mock_client.py` | `MockLLMClient` — deterministic canned JSON |
| `saturn/llm/anthropic_client.py` | `AnthropicClient` + pure `_build_params` helper |
| `saturn/ingestion/prices.py` | `fetch_company_data()` (yfinance + mock fixture) + `IngestionError` |
| `saturn/workflows/equity_research.py` | `analyze()`, `debate()`, `run()` orchestrator |
| `saturn/reports/markdown_report.py` | `render(ResearchReport) -> str` (pure) |
| `saturn/utils/logging.py` | `setup_logging()` |
| `saturn/cli.py` | Typer app, `research` command, `main()` entrypoint |
| `tests/*` | Offline tests (mock client + mock data) |
| `docs/`, `architecture/`, `README.md`, `CLAUDE.md` | Foundation docs |
| `examples/nvda_research_report.md` | Committed `--mock` sample output |

---

## Task 1: Project bootstrap (package, deps, venv)

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `reports/.gitkeep`
- Create: `saturn/__init__.py`, `saturn/llm/__init__.py`, `saturn/ingestion/__init__.py`, `saturn/workflows/__init__.py`, `saturn/reports/__init__.py`, `saturn/utils/__init__.py`
- Test: `tests/test_package_imports.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "saturn"
version = "0.0.1"
description = "AI-native autonomous equity research platform."
requires-python = ">=3.11"
dependencies = [
    "yfinance>=0.2.40",
    "anthropic>=0.40.0",
    "typer>=0.12.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
saturn = "saturn.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["saturn*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.pytest_cache/
.env
reports/*
!reports/.gitkeep
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Copy to .env and fill in. .env is gitignored — never commit real keys.
ANTHROPIC_API_KEY=
# Optional overrides:
# DEFAULT_MODEL=claude-sonnet-4-6
# REPORTS_DIR=reports
# LOG_LEVEL=INFO
```

- [ ] **Step 4: Create package `__init__.py` files and `reports/.gitkeep`**

`saturn/__init__.py`:
```python
"""Saturn — AI-native autonomous equity research platform."""

__version__ = "0.0.1"
```

Create empty files: `saturn/llm/__init__.py`, `saturn/ingestion/__init__.py`, `saturn/workflows/__init__.py`, `saturn/reports/__init__.py`, `saturn/utils/__init__.py`, and `reports/.gitkeep` (all empty).

- [ ] **Step 5: Create venv and install (editable + dev)**

PowerShell:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```
(bash equivalent: `python -m venv .venv && source .venv/Scripts/activate && python -m pip install -e ".[dev]"`)

Expected: installs yfinance, anthropic, typer, pydantic, pydantic-settings, pytest, and `saturn` (editable).

- [ ] **Step 6: Write the smoke test**

`tests/test_package_imports.py`:
```python
def test_import_saturn():
    import saturn

    assert saturn.__version__ == "0.0.1"
```

- [ ] **Step 7: Run the test**

Run: `python -m pytest tests/test_package_imports.py -v`
Expected: PASS (1 passed).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore .env.example reports/.gitkeep saturn/ tests/test_package_imports.py
git commit -m "chore: bootstrap saturn package, deps, and tooling"
```

---

## Task 2: Settings / config

**Files:**
- Create: `saturn/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from saturn.config import Settings, get_settings


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    s = Settings(_env_file=None)
    assert s.default_model == "claude-sonnet-4-6"
    assert s.anthropic_api_key is None
    assert s.log_level == "INFO"


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-4-7")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key == "test-key"
    assert s.default_model == "claude-opus-4-7"


def test_get_settings_returns_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert get_settings().anthropic_api_key == "k"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.config'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/config.py`:
```python
"""Application settings, loaded from environment and an optional .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    default_model: str = "claude-sonnet-4-6"
    reports_dir: Path = Path("reports")
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return a freshly-loaded Settings instance."""
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/config.py tests/test_config.py
git commit -m "feat(config): add Settings loaded from env/.env"
```

---

## Task 3: Pydantic data models

**Files:**
- Create: `saturn/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from datetime import date

from saturn.models import (
    AnalysisSections,
    CompanyData,
    DebateSections,
    NewsItem,
    ResearchReport,
)


def test_company_data_minimal_defaults():
    c = CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))
    assert c.ticker == "NVDA"
    assert c.segments == []
    assert c.metrics == {}
    assert c.news == []


def test_research_report_composes_sections():
    company = CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))
    analysis = AnalysisSections(
        executive_summary="ES",
        company_overview="CO",
        business_segments="BS",
        financial_snapshot="FS",
        valuation_discussion="VD",
        key_risks="KR",
        open_questions="OQ",
    )
    debate = DebateSections(bull_thesis="BULL", bear_thesis="BEAR", final_view="FV")
    report = ResearchReport(
        ticker="NVDA",
        company=company,
        analysis=analysis,
        debate=debate,
        generated_at=date(2026, 5, 25),
        model_used="mock",
        mock=True,
        sources=["s1"],
    )
    assert report.analysis.key_risks == "KR"
    assert report.debate.final_view == "FV"
    assert report.mock is True


def test_news_item_optional_fields():
    n = NewsItem(title="Headline")
    assert n.title == "Headline"
    assert n.link is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.models'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/models.py`:
```python
"""Typed data models shared across the Saturn pipeline."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    title: str
    publisher: str | None = None
    link: str | None = None
    published: str | None = None


class CompanyData(BaseModel):
    """Structured company facts produced by ingestion (real or mock)."""

    ticker: str
    name: str
    sector: str | None = None
    industry: str | None = None
    business_summary: str | None = None
    segments: list[str] = Field(default_factory=list)
    price: float | None = None
    currency: str | None = None
    market_cap: float | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
    news: list[NewsItem] = Field(default_factory=list)
    as_of: date


class AnalysisSections(BaseModel):
    """Reasoned sections produced by the `analyze` LLM call."""

    executive_summary: str
    company_overview: str
    business_segments: str
    financial_snapshot: str
    valuation_discussion: str
    key_risks: str
    open_questions: str


class DebateSections(BaseModel):
    """Bull/bear/synthesis produced by the `debate` LLM call."""

    bull_thesis: str
    bear_thesis: str
    final_view: str


class ResearchReport(BaseModel):
    """The fully-composed research report, ready to render."""

    ticker: str
    company: CompanyData
    analysis: AnalysisSections
    debate: DebateSections
    generated_at: date
    model_used: str
    mock: bool
    sources: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models.py
git commit -m "feat(models): add Pydantic models for company data and report"
```

---

## Task 4: LLM interface + mock client

**Files:**
- Create: `saturn/llm/base.py`, `saturn/llm/mock_client.py`
- Test: `tests/test_mock_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mock_client.py`:
```python
import json

from saturn.llm.mock_client import MockLLMClient


def test_mock_returns_analysis_json():
    client = MockLLMClient()
    raw = client.complete("sys", "OUTPUT_SCHEMA=analysis\nplease analyze")
    data = json.loads(raw)
    assert set(data) == {
        "executive_summary",
        "company_overview",
        "business_segments",
        "financial_snapshot",
        "valuation_discussion",
        "key_risks",
        "open_questions",
    }
    assert data["executive_summary"].startswith("[MOCK]")


def test_mock_returns_debate_json():
    client = MockLLMClient()
    raw = client.complete("sys", "OUTPUT_SCHEMA=debate\nplease debate")
    data = json.loads(raw)
    assert set(data) == {"bull_thesis", "bear_thesis", "final_view"}
    assert data["bull_thesis"].startswith("[MOCK]")


def test_mock_is_deterministic():
    client = MockLLMClient()
    a = client.complete("s", "OUTPUT_SCHEMA=analysis")
    b = client.complete("s", "OUTPUT_SCHEMA=analysis")
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mock_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.llm.mock_client'`.

- [ ] **Step 3: Write the implementations**

`saturn/llm/base.py`:
```python
"""Provider-agnostic LLM interface."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Minimal text-completion interface implemented by all providers."""

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str:
        """Return the model's text response to `prompt` under `system`."""
        ...
```

`saturn/llm/mock_client.py`:
```python
"""Deterministic offline LLM client for --mock runs and tests."""

from __future__ import annotations

import json

_ANALYSIS = json.dumps(
    {
        "executive_summary": "[MOCK] Executive summary placeholder for offline/testing mode.",
        "company_overview": "[MOCK] Company overview placeholder.",
        "business_segments": "[MOCK] Business segments placeholder.",
        "financial_snapshot": "[MOCK] Financial commentary placeholder.",
        "valuation_discussion": "[MOCK] Valuation discussion placeholder.",
        "key_risks": "[MOCK] Key risks placeholder.",
        "open_questions": "[MOCK] Open questions placeholder.",
    }
)

_DEBATE = json.dumps(
    {
        "bull_thesis": "[MOCK] Bull thesis placeholder.",
        "bear_thesis": "[MOCK] Bear thesis placeholder.",
        "final_view": "[MOCK] Balanced final view placeholder.",
    }
)


class MockLLMClient:
    """Returns fixed JSON keyed by the OUTPUT_SCHEMA tag in the prompt."""

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str:
        if "OUTPUT_SCHEMA=debate" in prompt:
            return _DEBATE
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return _ANALYSIS
        return "{}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mock_client.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/llm/base.py saturn/llm/mock_client.py tests/test_mock_client.py
git commit -m "feat(llm): add LLMClient protocol and deterministic MockLLMClient"
```

---

## Task 5: Logging setup

**Files:**
- Create: `saturn/utils/logging.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing test**

`tests/test_logging.py`:
```python
import logging

from saturn.utils.logging import setup_logging


def test_setup_logging_sets_level():
    setup_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING
    setup_logging("INFO")
    assert logging.getLogger().level == logging.INFO


def test_setup_logging_bad_level_defaults_to_info():
    setup_logging("NOPE")
    assert logging.getLogger().level == logging.INFO
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logging.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.utils.logging'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/utils/logging.py`:
```python
"""Structured logging configuration."""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging. Unknown levels fall back to INFO."""
    resolved = getattr(logging, level.upper(), logging.INFO)
    if not isinstance(resolved, int):
        resolved = logging.INFO
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logging.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/utils/logging.py tests/test_logging.py
git commit -m "feat(utils): add setup_logging"
```

---

## Task 6: Markdown report renderer

**Files:**
- Create: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_markdown_report.py`:
```python
from datetime import date

from saturn.models import (
    AnalysisSections,
    CompanyData,
    DebateSections,
    NewsItem,
    ResearchReport,
)
from saturn.reports.markdown_report import render


def _sample_report() -> ResearchReport:
    company = CompanyData(
        ticker="NVDA",
        name="NVIDIA",
        as_of=date(2026, 5, 25),
        price=900.0,
        currency="USD",
        market_cap=2_200_000_000_000,
        metrics={"trailing_pe": 65.0},
        news=[NewsItem(title="N1", link="https://x", publisher="P")],
    )
    analysis = AnalysisSections(
        executive_summary="ES",
        company_overview="CO",
        business_segments="BS",
        financial_snapshot="FS",
        valuation_discussion="VD",
        key_risks="KR",
        open_questions="OQ",
    )
    debate = DebateSections(bull_thesis="BULL", bear_thesis="BEAR", final_view="FV")
    return ResearchReport(
        ticker="NVDA",
        company=company,
        analysis=analysis,
        debate=debate,
        generated_at=date(2026, 5, 25),
        model_used="mock",
        mock=True,
        sources=["s1"],
    )


def test_render_has_all_thirteen_sections():
    md = render(_sample_report())
    expected = [
        "# NVDA Equity Research Report",
        "## 1. Executive Summary",
        "## 2. Company Overview",
        "## 3. Business Segments",
        "## 4. Recent Market Performance",
        "## 5. Financial Snapshot",
        "## 6. Recent News and Catalysts",
        "## 7. Bull Thesis",
        "## 8. Bear Thesis",
        "## 9. Key Risks",
        "## 10. Valuation Discussion",
        "## 11. Open Questions",
        "## 12. Final View",
        "## 13. Sources",
    ]
    for header in expected:
        assert header in md, f"missing: {header}"


def test_render_includes_disclaimer_and_content():
    md = render(_sample_report())
    assert "not investment advice" in md
    assert "BULL" in md and "BEAR" in md
    assert "[N1](https://x)" in md
    assert "MOCK DATA" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_markdown_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.reports.markdown_report'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/reports/markdown_report.py`:
```python
"""Render a ResearchReport into markdown."""

from __future__ import annotations

from saturn.models import ResearchReport

_DISCLAIMER = (
    "*This report is for research and educational purposes only "
    "and is not investment advice.*"
)


def _fmt_money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def render(report: ResearchReport) -> str:
    """Return the markdown for a research report (pure function)."""
    c = report.company
    a = report.analysis
    d = report.debate
    out: list[str] = []

    out.append(f"# {report.ticker} Equity Research Report")
    out.append("")
    meta = f"*Generated {report.generated_at:%Y-%m-%d} · model: {report.model_used}"
    if report.mock:
        meta += " · MOCK DATA"
    meta += "*"
    out.append(meta)
    out.append("")

    out += ["## 1. Executive Summary", "", a.executive_summary, ""]
    out += ["## 2. Company Overview", "", a.company_overview, ""]
    out += ["## 3. Business Segments", "", a.business_segments, ""]

    out += ["## 4. Recent Market Performance", ""]
    out.append(f"- Price: {_fmt_money(c.price)} {c.currency or ''}".rstrip())
    out.append(f"- Market cap: {_fmt_money(c.market_cap)}")
    out.append("")

    out += ["## 5. Financial Snapshot", ""]
    if c.metrics:
        out.append("| Metric | Value |")
        out.append("| --- | --- |")
        for key, value in c.metrics.items():
            out.append(f"| {key} | {value if value is not None else 'N/A'} |")
        out.append("")
    out += [a.financial_snapshot, ""]

    out += ["## 6. Recent News and Catalysts", ""]
    if c.news:
        for item in c.news:
            suffix = f" — {item.publisher}" if item.publisher else ""
            if item.link:
                out.append(f"- [{item.title}]({item.link}){suffix}")
            else:
                out.append(f"- {item.title}{suffix}")
    else:
        out.append("_No recent news available._")
    out.append("")

    out += ["## 7. Bull Thesis", "", d.bull_thesis, ""]
    out += ["## 8. Bear Thesis", "", d.bear_thesis, ""]
    out += ["## 9. Key Risks", "", a.key_risks, ""]
    out += ["## 10. Valuation Discussion", "", a.valuation_discussion, ""]
    out += ["## 11. Open Questions", "", a.open_questions, ""]
    out += ["## 12. Final View", "", d.final_view, ""]

    out += ["## 13. Sources", ""]
    if report.sources:
        out += [f"- {s}" for s in report.sources]
    else:
        out.append("_No sources recorded._")
    out.append("")

    out += ["---", "", _DISCLAIMER, ""]
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_markdown_report.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(reports): render ResearchReport to 13-section markdown"
```

---

## Task 7: Equity research workflow (orchestrator)

**Files:**
- Create: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing test**

`tests/test_equity_research_workflow.py`:
```python
from datetime import date

from saturn.llm.mock_client import MockLLMClient
from saturn.models import CompanyData
from saturn.workflows.equity_research import _extract_json, run


def _company() -> CompanyData:
    return CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))


def test_run_with_mock_client_populates_report():
    report = run(_company(), MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.mock is True
    assert report.model_used == "mock"
    assert report.analysis.executive_summary.startswith("[MOCK]")
    assert report.debate.bull_thesis.startswith("[MOCK]")
    assert report.sources == ["MOCK fixture data — not real market sources"]


def test_run_real_mode_builds_yfinance_source():
    report = run(_company(), MockLLMClient(), model_used="claude-x", mock=False)
    assert report.sources[0] == "yfinance (price, profile, financials)"


def test_extract_json_strips_code_fences():
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json(fenced) == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_equity_research_workflow.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.workflows.equity_research'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/workflows/equity_research.py`:
```python
"""Sequential equity-research pipeline: analyze -> debate -> assemble."""

from __future__ import annotations

import logging
from datetime import date

from saturn.llm.base import LLMClient
from saturn.models import (
    AnalysisSections,
    CompanyData,
    DebateSections,
    ResearchReport,
)

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM = (
    "You are a rigorous buy-side equity research analyst. Base every statement "
    "only on the provided company data. Do not invent figures. Be concise and "
    "balanced. Respond with ONLY a valid JSON object, no prose, no code fences."
)

DEBATE_SYSTEM = (
    "You run a structured bull/bear debate for an equity. Build the strongest "
    "honest case for each side from the provided data, then a balanced final "
    "view. Respond with ONLY a valid JSON object, no prose, no code fences."
)


def _company_context(company: CompanyData) -> str:
    return company.model_dump_json(indent=2)


def _extract_json(text: str) -> str:
    """Strip surrounding ```/```json code fences if present."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def analyze(
    company: CompanyData, llm: LLMClient, *, model: str | None = None
) -> AnalysisSections:
    prompt = (
        "OUTPUT_SCHEMA=analysis\n"
        f"Company data (JSON):\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "executive_summary, company_overview, business_segments, "
        "financial_snapshot, valuation_discussion, key_risks, open_questions."
    )
    logger.info("analyze: %s", company.ticker)
    raw = llm.complete(ANALYSIS_SYSTEM, prompt, model=model)
    return AnalysisSections.model_validate_json(_extract_json(raw))


def debate(
    company: CompanyData, llm: LLMClient, *, model: str | None = None
) -> DebateSections:
    prompt = (
        "OUTPUT_SCHEMA=debate\n"
        f"Company data (JSON):\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "bull_thesis, bear_thesis, final_view."
    )
    logger.info("debate: %s", company.ticker)
    raw = llm.complete(DEBATE_SYSTEM, prompt, model=model)
    return DebateSections.model_validate_json(_extract_json(raw))


def _build_sources(company: CompanyData, *, mock: bool) -> list[str]:
    if mock:
        return ["MOCK fixture data — not real market sources"]
    sources = ["yfinance (price, profile, financials)"]
    sources += [item.link for item in company.news if item.link]
    return sources


def run(
    company: CompanyData,
    llm: LLMClient,
    *,
    model_used: str,
    mock: bool,
) -> ResearchReport:
    """Run the full pipeline and return an assembled ResearchReport."""
    call_model = None if mock else model_used
    analysis = analyze(company, llm, model=call_model)
    deb = debate(company, llm, model=call_model)
    return ResearchReport(
        ticker=company.ticker,
        company=company,
        analysis=analysis,
        debate=deb,
        generated_at=date.today(),
        model_used=model_used,
        mock=mock,
        sources=_build_sources(company, mock=mock),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_equity_research_workflow.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflows): add analyze/debate/run equity research pipeline"
```

---

## Task 8: Data ingestion (yfinance + mock fixture)

**Files:**
- Create: `saturn/ingestion/prices.py`
- Test: `tests/test_ingestion.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ingestion.py`:
```python
from saturn.ingestion.prices import IngestionError, fetch_company_data
from saturn.models import CompanyData


def test_fetch_mock_returns_nvidia_fixture():
    c = fetch_company_data("NVDA", mock=True)
    assert isinstance(c, CompanyData)
    assert c.ticker == "NVDA"
    assert c.name == "NVIDIA Corporation"
    assert "Data Center" in c.segments
    assert c.price is not None
    assert c.news and c.news[0].title.startswith("[MOCK]")


def test_fetch_mock_preserves_ticker_case():
    c = fetch_company_data("msft", mock=True)
    assert c.ticker == "msft"


def test_ingestion_error_is_runtime_error():
    assert issubclass(IngestionError, RuntimeError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ingestion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.prices'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/ingestion/prices.py`:
```python
"""Company data ingestion from yfinance, with an offline mock fixture."""

from __future__ import annotations

import logging
from datetime import date

from saturn.models import CompanyData, NewsItem

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when company data cannot be fetched."""


def _mock_company(ticker: str) -> CompanyData:
    return CompanyData(
        ticker=ticker,
        name="NVIDIA Corporation",
        sector="Technology",
        industry="Semiconductors",
        business_summary="[MOCK] Designs GPUs and accelerated computing platforms.",
        segments=["Data Center", "Gaming", "Professional Visualization", "Automotive"],
        price=900.0,
        currency="USD",
        market_cap=2_200_000_000_000,
        metrics={
            "trailing_pe": 65.0,
            "revenue_growth": 1.2,
            "profit_margin": 0.48,
            "free_cashflow": 27_000_000_000.0,
        },
        news=[
            NewsItem(
                title="[MOCK] NVIDIA announces next-gen architecture",
                publisher="MockWire",
                link="https://example.com/mock",
            )
        ],
        as_of=date.today(),
    )


def _extract_news(raw_news: list) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in (raw_news or [])[:5]:
        content = entry.get("content", entry) if isinstance(entry, dict) else {}
        provider = content.get("provider")
        canonical = content.get("canonicalUrl")
        items.append(
            NewsItem(
                title=content.get("title") or entry.get("title") or "Untitled",
                publisher=(
                    provider.get("displayName")
                    if isinstance(provider, dict)
                    else entry.get("publisher")
                ),
                link=(
                    canonical.get("url")
                    if isinstance(canonical, dict)
                    else entry.get("link")
                ),
            )
        )
    return items


def fetch_company_data(ticker: str, *, mock: bool = False) -> CompanyData:
    """Return CompanyData for `ticker`. Use mock=True for offline fixture data."""
    if mock:
        logger.info("ingest(mock): %s", ticker)
        return _mock_company(ticker)

    logger.info("ingest(yfinance): %s", ticker)
    try:
        import yfinance as yf

        handle = yf.Ticker(ticker)
        info = handle.info or {}
    except Exception as exc:  # noqa: BLE001 - surface as a typed error
        raise IngestionError(
            f"Could not fetch data for {ticker}. Check the ticker or run with --mock."
        ) from exc

    if not (info.get("shortName") or info.get("longName") or info.get("symbol")):
        raise IngestionError(
            f"Could not fetch data for {ticker}. Check the ticker or run with --mock."
        )

    try:
        raw_news = handle.news
    except Exception:  # noqa: BLE001 - news is best-effort
        raw_news = []

    return CompanyData(
        ticker=ticker,
        name=info.get("longName") or info.get("shortName") or ticker,
        sector=info.get("sector"),
        industry=info.get("industry"),
        business_summary=info.get("longBusinessSummary"),
        segments=[],
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        currency=info.get("currency"),
        market_cap=info.get("marketCap"),
        metrics={
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "free_cashflow": info.get("freeCashflow"),
            "total_debt": info.get("totalDebt"),
        },
        news=_extract_news(raw_news),
        as_of=date.today(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ingestion.py -v`
Expected: PASS (3 passed).

> Note: the real yfinance path is exercised manually in Task 11 (`saturn research NVDA` without `--mock`), not in the offline test suite.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/prices.py tests/test_ingestion.py
git commit -m "feat(ingestion): fetch company data via yfinance with mock fixture"
```

---

## Task 9: Anthropic LLM client

**Files:**
- Create: `saturn/llm/anthropic_client.py`
- Test: `tests/test_anthropic_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_anthropic_client.py`:
```python
from saturn.llm.anthropic_client import _build_params


def test_build_params_sets_model_and_messages():
    params = _build_params("system text", "user prompt", "claude-test")
    assert params["model"] == "claude-test"
    assert params["max_tokens"] > 0
    assert params["messages"] == [{"role": "user", "content": "user prompt"}]


def test_build_params_uses_prompt_caching_on_system():
    params = _build_params("system text", "user prompt", "claude-test")
    system_block = params["system"][0]
    assert system_block["text"] == "system text"
    assert system_block["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_anthropic_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.llm.anthropic_client'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/llm/anthropic_client.py`:
```python
"""Anthropic-backed LLMClient with prompt caching on the system prompt."""

from __future__ import annotations


def _build_params(
    system: str, prompt: str, model: str, *, max_tokens: int = 2000
) -> dict:
    """Build the kwargs for Anthropic messages.create (pure, testable)."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": prompt}],
    }


class AnthropicClient:
    """LLMClient implementation backed by the Anthropic SDK."""

    def __init__(self, api_key: str, default_model: str) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self._default_model = default_model

    def complete(self, system: str, prompt: str, *, model: str | None = None) -> str:
        params = _build_params(system, prompt, model or self._default_model)
        response = self._client.messages.create(**params)
        return "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_anthropic_client.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add saturn/llm/anthropic_client.py tests/test_anthropic_client.py
git commit -m "feat(llm): add AnthropicClient with prompt caching"
```

---

## Task 10: CLI (`saturn research`)

**Files:**
- Create: `saturn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from typer.testing import CliRunner

from saturn.cli import app

runner = CliRunner()


def test_research_mock_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    result = runner.invoke(app, ["research", "nvda", "--mock"])
    assert result.exit_code == 0, result.output
    files = list(tmp_path.glob("NVDA_*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# NVDA Equity Research Report" in text
    assert "not investment advice" in text
    assert "MOCK MODE" in result.output


def test_research_real_without_key_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["research", "NVDA"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.cli'`.

- [ ] **Step 3: Write minimal implementation**

`saturn/cli.py`:
```python
"""Saturn command-line interface."""

from __future__ import annotations

from datetime import date

import typer

from saturn.config import get_settings
from saturn.ingestion.prices import IngestionError, fetch_company_data
from saturn.llm.anthropic_client import AnthropicClient
from saturn.llm.mock_client import MockLLMClient
from saturn.reports.markdown_report import render
from saturn.utils.logging import setup_logging
from saturn.workflows.equity_research import run

app = typer.Typer(help="Saturn — autonomous equity research.")


@app.callback()
def _main() -> None:
    """Saturn — autonomous equity research (keeps `research` as a subcommand)."""


@app.command()
def research(
    ticker: str = typer.Argument(..., help="Stock ticker, e.g. NVDA"),
    mock: bool = typer.Option(False, "--mock", help="Run fully offline with sample data."),
    model: str | None = typer.Option(None, "--model", help="Override the LLM model."),
) -> None:
    """Generate a markdown equity research report for TICKER."""
    settings = get_settings()
    setup_logging(settings.log_level)
    ticker = ticker.upper()

    # Resolve the LLM client first so config errors (e.g. missing key) fail
    # fast, before any network ingestion. This also keeps the no-key path
    # fully offline for tests.
    if mock:
        llm = MockLLMClient()
        model_used = "mock"
    else:
        if not settings.anthropic_api_key:
            typer.echo(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env, "
                "or run with --mock for offline output.",
                err=True,
            )
            raise typer.Exit(1)
        model_used = model or settings.default_model
        llm = AnthropicClient(settings.anthropic_api_key, settings.default_model)

    try:
        company = fetch_company_data(ticker, mock=mock)
    except IngestionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    report = run(company, llm, model_used=model_used, mock=mock)
    markdown = render(report)

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.reports_dir / f"{ticker}_{date.today():%Y-%m-%d}.md"
    out_path.write_text(markdown, encoding="utf-8")

    banner = "[MOCK MODE] " if mock else ""
    typer.echo(f"{banner}Wrote {out_path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add saturn/cli.py tests/test_cli.py
git commit -m "feat(cli): add `saturn research` command with --mock fallback"
```

---

## Task 11: End-to-end run + committed example report

**Files:**
- Create: `examples/nvda_research_report.md`

- [ ] **Step 1: Run the mock command end-to-end**

Run: `python -m saturn.cli research NVDA --mock`
Expected output: `[MOCK MODE] Wrote reports\NVDA_2026-05-25.md` (date = today).
Verify the file exists and contains all 13 section headers + disclaimer.

- [ ] **Step 2: (Optional, requires key) Run the real command**

Only if `ANTHROPIC_API_KEY` is set in `.env`:
Run: `python -m saturn.cli research NVDA`
Expected: `Wrote reports\NVDA_<today>.md` populated from live yfinance data + LLM analysis. If it errors on data, confirm `--mock` still works and note the data issue for a later task — do not block.

- [ ] **Step 3: Copy the mock output to examples/**

PowerShell:
```powershell
Copy-Item reports\NVDA_*.md examples\nvda_research_report.md
```
(bash: `cp reports/NVDA_*.md examples/nvda_research_report.md`)

- [ ] **Step 4: Commit the example**

```bash
git add examples/nvda_research_report.md
git commit -m "docs: add committed mock NVDA research report example"
```

---

## Task 12: Foundation docs

**Files:**
- Create: `CLAUDE.md`, `README.md` (expand), `docs/vision.md`, `docs/roadmap.md`, `docs/engineering_principles.md`, `architecture/system_overview.md`

- [ ] **Step 1: Create `CLAUDE.md`**

Paste the full project-context document the user authored (the "Saturn Project Context" markdown) verbatim into `CLAUDE.md` at the repo root.

- [ ] **Step 2: Expand `README.md`**

```markdown
# Saturn

An AI-native autonomous equity research platform. Saturn coordinates a pipeline
(and, over time, multiple specialized agents) to ingest financial data, analyze
companies, debate theses, and generate research reports.

> Research and educational use only. Not investment advice.

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
python -m pip install -e ".[dev]"

# Offline demo (no API key needed):
saturn research NVDA --mock

# Real run (set ANTHROPIC_API_KEY in .env first):
saturn research NVDA
```

Reports are written to `reports/<TICKER>_<YYYY-MM-DD>.md`.
See `examples/nvda_research_report.md` for sample output.

## Status

Phase 0 (foundation + MVP). See `docs/roadmap.md` for the full plan and
`docs/superpowers/specs/` + `docs/superpowers/plans/` for design and build docs.

## Development

```bash
python -m pytest          # offline test suite (no network, no API key)
```
```

- [ ] **Step 3: Create `docs/vision.md`**

```markdown
# Saturn Vision

Saturn is an AI-native autonomous equity research platform. The long-term goal
is a system that behaves like *Bloomberg Terminal + junior research analysts +
an AI portfolio manager + persistent memory* — operating continuously rather
than answering one-off questions.

## Purposes

1. **Personal financial research** — analyze companies, track earnings,
   summarize filings/transcripts, monitor news/macro, generate bull/bear
   theses, maintain a watchlist, and update prior research as data arrives.
2. **AI infra / data-platform practice** — agent orchestration, long-running
   workflows, ingestion pipelines, RAG, vector memory, observability, and eval.
3. **Multi-agent engineering workflow** — practice using AI agents to design,
   build, review, debug, and document the system itself.

Focus: equities, macro, finance. Web3 is not a core direction (BTC/crypto-macro
only when relevant). Reports are research/educational only — not advice.
```

- [ ] **Step 4: Create `docs/roadmap.md`**

```markdown
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
```

- [ ] **Step 5: Create `docs/engineering_principles.md`**

```markdown
# Engineering Principles

- **Keep it runnable.** Prefer a simple working version over an impressive but
  broken architecture.
- **Local-first.** The system runs from the command line first.
- **Docs-as-code.** Important decisions live in the repo (specs, plans, RFCs).
- **AI-readable repo.** Clear modules and interfaces so coding agents orient fast.
- **Avoid premature complexity.** No Kafka/Kubernetes/Airflow/Temporal/LangGraph
  until there is a clear reason.
- **Production thinking, even locally.** Clear modules, interfaces, tests, logs,
  config management, error handling, reproducibility.
- **Style.** Readable Python over clever Python; type hints; Pydantic for
  structured data; small focused functions/modules; no hidden global state;
  secrets via env vars; never commit API keys.
```

- [ ] **Step 6: Create `architecture/system_overview.md`**

```markdown
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
```

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md README.md docs/vision.md docs/roadmap.md docs/engineering_principles.md architecture/system_overview.md
git commit -m "docs: add CLAUDE.md, README, vision, roadmap, principles, architecture"
```

---

## Done criteria

- [ ] `python -m pytest -v` — all tests pass offline (no network, no API key).
- [ ] `saturn research NVDA --mock` writes `reports/NVDA_<today>.md` with all 13 sections + disclaimer.
- [ ] `examples/nvda_research_report.md` is committed.
- [ ] Foundation docs (`CLAUDE.md`, `README.md`, `docs/`, `architecture/`) are committed.
- [ ] (If key available) `saturn research NVDA` produces a real-data report.

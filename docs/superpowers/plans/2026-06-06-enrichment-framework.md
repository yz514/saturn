# Enrichment Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the thin `CompanyData` agent-context with a provenance-tagged `CompanyDossier` assembled through a soft-fail dispatcher, delivering a working enriched pipeline on `--mock` plus a real yfinance quote, with seams ready for the EDGAR and FRED adapters.

**Architecture:** A vendor-neutral canonical model (every datum carries `Provenance`) lives in `saturn/models.py`. Source adapters return canonical objects; a `route_to_source` dispatcher calls them and converts failures into recorded `SourceGap`s instead of crashing. `build_dossier` orchestrates adapters into a `CompanyDossier`, which the analyze/debate pipeline renders to text **with inline provenance** (the F1 fix) and the report renderer surfaces. This plan wires the quote adapter (yfinance) for real; EDGAR and FRED are registered as not-yet-available and degrade to gaps until their own plans land.

**Tech Stack:** Python 3.13, Pydantic v2, pydantic-settings, Typer, yfinance, pytest. No new runtime dependency is required (HTTP-based EDGAR/FRED adapters arrive in later plans).

**Scope note:** This is Slice-1 *framework only*. The real EDGAR adapter (companyfacts + 10-K sections) and FRED adapter are separate plans (`2026-06-06-edgar-adapter.md`, `2026-06-06-fred-adapter.md`) that implement the `edgar` and `fred` source functions behind the dispatcher built here. Spec: `docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md`.

---

## File Structure

**Create:**
- `saturn/ingestion/errors.py` — typed exception hierarchy (`IngestionError`, `DataUnavailable`, `SourceFailure`).
- `saturn/ingestion/cache.py` — per-source TTL disk cache for raw + canonical JSON under `data/cache/`.
- `saturn/ingestion/dispatch.py` — `route_to_source()` soft-fail wrapper returning `(result, gap)`.
- `saturn/ingestion/dossier.py` — `build_dossier()` orchestration + `_mock_dossier()` fixture.
- `tests/ingestion/test_cache.py`, `tests/ingestion/test_dispatch.py`, `tests/ingestion/test_dossier.py`, `tests/ingestion/test_quote.py`
- `tests/__init__.py` and `tests/ingestion/__init__.py` only if the existing suite uses package dirs (it does not — tests are bare modules; do **not** add `__init__.py`).

**Modify:**
- `saturn/models.py` — add `Provenance`, `Quote`, `FinancialFact`, `Fundamentals`, `FilingSection`, `MacroSeries`, `MacroSnapshot`, `SourceGap`, `CompanyDossier`; change `ResearchReport.company` to `CompanyDossier`.
- `saturn/config.py` — add `fred_api_key`, `sec_user_agent`.
- `saturn/ingestion/prices.py` — add `fetch_quote(ticker, *, mock) -> Quote`; move `IngestionError` to `errors.py` (re-export for back-compat).
- `saturn/workflows/equity_research.py` — `analyze`/`debate`/`run`/`_company_context`/`_build_sources` operate on `CompanyDossier`; context rendered with inline provenance.
- `saturn/reports/markdown_report.py` — render from `CompanyDossier` (quote, financials table from `Fundamentals`, macro snapshot, gaps).
- `saturn/cli.py` — call `build_dossier` instead of `fetch_company_data`.
- `tests/conftest.py` — neutralize `FRED_API_KEY` and `SEC_USER_AGENT`.
- `.env.example` — document `FRED_API_KEY`, `SEC_USER_AGENT`.
- `.gitignore` — add `data/cache/`.

**Established patterns to follow:** All shared models live in `saturn/models.py` (single contract file). Tests are bare modules under `tests/` (no package `__init__.py`), rely on the autouse `offline_settings` fixture, and never hit the network. Adapters lazy-import their network library inside the function (see `prices.py` importing `yfinance` inside `fetch_company_data`).

---

## Task 1: Canonical model in `saturn/models.py`

**Files:**
- Modify: `saturn/models.py` (add new models after `NewsItem`, before `AnalysisSections`; change `ResearchReport.company`)
- Test: `tests/test_models.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from datetime import date

from saturn.models import (
    CompanyDossier,
    FinancialFact,
    Fundamentals,
    Provenance,
    Quote,
    SourceGap,
)


def test_provenance_defaults_optional():
    p = Provenance(source="FRED")
    assert p.source == "FRED"
    assert p.source_url is None and p.as_of is None and p.retrieved_at is None


def test_financial_fact_carries_provenance():
    fact = FinancialFact(
        concept="Revenues",
        value=1000.0,
        unit="USD",
        fiscal_period="FY2024",
        provenance=Provenance(source="SEC EDGAR", as_of=date(2025, 2, 1)),
    )
    assert fact.provenance.source == "SEC EDGAR"


def test_dossier_minimal_construction():
    d = CompanyDossier(
        ticker="NVDA",
        name="NVIDIA Corporation",
        generated_at=date(2026, 6, 6),
    )
    assert d.quote is None
    assert d.fundamentals is None
    assert d.filing_sections == []
    assert d.gaps == []


def test_dossier_with_quote_and_facts():
    d = CompanyDossier(
        ticker="NVDA",
        name="NVIDIA Corporation",
        quote=Quote(price=900.0, currency="USD", provenance=Provenance(source="yfinance")),
        fundamentals=Fundamentals(
            facts=[
                FinancialFact(concept="Revenues", value=60.0, provenance=Provenance(source="SEC EDGAR"))
            ]
        ),
        gaps=[SourceGap(source="FRED", reason="not configured")],
        generated_at=date(2026, 6, 6),
    )
    assert d.quote.price == 900.0
    assert d.fundamentals.facts[0].concept == "Revenues"
    assert d.gaps[0].source == "FRED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'CompanyDossier'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/models.py`, after the `NewsItem` class and before `class CompanyData`, add:

```python
class Provenance(BaseModel):
    """Lineage for a single datum: where it came from and when."""

    source: str
    source_url: str | None = None
    as_of: date | None = None
    retrieved_at: date | None = None


class Quote(BaseModel):
    price: float | None = None
    market_cap: float | None = None
    currency: str | None = None
    provenance: Provenance


class FinancialFact(BaseModel):
    concept: str
    value: float | None = None
    unit: str | None = None
    fiscal_period: str | None = None
    provenance: Provenance


class Fundamentals(BaseModel):
    facts: list[FinancialFact] = Field(default_factory=list)


class FilingSection(BaseModel):
    name: str
    excerpt: str
    full_text_cache_ref: str | None = None
    provenance: Provenance


class MacroSeries(BaseModel):
    series_id: str
    title: str
    observations: list[tuple[date, float]] = Field(default_factory=list)
    provenance: Provenance


class MacroSnapshot(BaseModel):
    series: list[MacroSeries] = Field(default_factory=list)


class SourceGap(BaseModel):
    """A source that could not contribute, recorded instead of crashing."""

    source: str
    reason: str


class CompanyDossier(BaseModel):
    """Rich, provenance-tagged evidence envelope consumed by the agents."""

    ticker: str
    cik: str | None = None
    name: str
    sector: str | None = None
    industry: str | None = None
    business_summary: str | None = None
    segments: list[str] = Field(default_factory=list)
    quote: Quote | None = None
    fundamentals: Fundamentals | None = None
    filing_sections: list[FilingSection] = Field(default_factory=list)
    macro: MacroSnapshot | None = None
    news: list[NewsItem] = Field(default_factory=list)
    gaps: list[SourceGap] = Field(default_factory=list)
    generated_at: date
```

Then change the `ResearchReport.company` field type from `CompanyData` to `CompanyDossier`:

```python
class ResearchReport(BaseModel):
    """The fully-composed research report, ready to render."""

    ticker: str
    company: CompanyDossier
    analysis: AnalysisSections
    debate: DebateSections
    generated_at: date
    model_used: str
    mock: bool
    sources: list[str] = Field(default_factory=list)
```

Keep the existing `CompanyData` class as-is (still used by `fetch_company_data` until removed).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models.py
git commit -m "feat(models): add provenance-tagged CompanyDossier canonical model"
```

---

## Task 2: Typed error hierarchy in `saturn/ingestion/errors.py`

**Files:**
- Create: `saturn/ingestion/errors.py`
- Modify: `saturn/ingestion/prices.py` (import `IngestionError` from new module)
- Test: `tests/ingestion/test_errors.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_errors.py`:

```python
from saturn.ingestion.errors import DataUnavailable, IngestionError, SourceFailure


def test_subclasses_of_ingestion_error():
    assert issubclass(DataUnavailable, IngestionError)
    assert issubclass(SourceFailure, IngestionError)


def test_prices_reexports_same_class():
    from saturn.ingestion.prices import IngestionError as PricesIngestionError

    assert PricesIngestionError is IngestionError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.errors'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/errors.py`:

```python
"""Typed ingestion errors.

`DataUnavailable` means the source was reachable but the datum is genuinely
absent (e.g. no CIK for a ticker). `SourceFailure` means a transport/rate-limit
error. The dispatcher uses the distinction to decide whether a missing source is
a recorded gap (both cases here) versus something a caller might retry.
"""

from __future__ import annotations


class IngestionError(RuntimeError):
    """Base class for all ingestion failures."""


class DataUnavailable(IngestionError):
    """The source responded but the requested datum does not exist."""


class SourceFailure(IngestionError):
    """A network, rate-limit, or transport error reaching the source."""
```

In `saturn/ingestion/prices.py`, replace the local class definition:

```python
class IngestionError(RuntimeError):
    """Raised when company data cannot be fetched."""
```

with an import near the top (after the existing `from saturn.models import ...` line):

```python
from saturn.ingestion.errors import IngestionError
```

Remove the now-duplicate `class IngestionError(RuntimeError):` definition from `prices.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_errors.py tests/test_ingestion_prices.py -v`
Expected: PASS (both the new errors tests and the existing prices tests, proving the re-export didn't break callers). If the existing prices test file has a different name, run the whole suite instead: `.venv\Scripts\python.exe -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/errors.py saturn/ingestion/prices.py tests/ingestion/test_errors.py
git commit -m "feat(ingestion): add typed DataUnavailable/SourceFailure error hierarchy"
```

---

## Task 3: Config + offline guard for new keys

**Files:**
- Modify: `saturn/config.py`
- Modify: `tests/conftest.py`
- Modify: `.env.example`
- Test: `tests/test_config.py` (create or extend)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from saturn.config import get_settings


def test_new_keys_default_none(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    s = get_settings()
    assert s.fred_api_key is None
    assert s.sec_user_agent is None


def test_new_keys_read_from_env(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    monkeypatch.setenv("SEC_USER_AGENT", "Saturn test@example.com")
    s = get_settings()
    assert s.fred_api_key == "abc123"
    assert s.sec_user_agent == "Saturn test@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'fred_api_key'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/config.py`, add two fields to `Settings` after `anthropic_api_key`:

```python
    anthropic_api_key: str | None = None
    fred_api_key: str | None = None
    sec_user_agent: str | None = None
    default_model: str = "claude-sonnet-4-6"
```

In `tests/conftest.py`, extend the autouse fixture to drop the new keys:

```python
@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    # Ignore any real .env file during tests, and drop shell-exported keys.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
```

In `.env.example`, add below `ANTHROPIC_API_KEY=`:

```text
# Optional data-source keys (Slice 1 enrichment):
# FRED_API_KEY=            # free key from https://fred.stlouisfed.org/docs/api/api_key.html
# SEC_USER_AGENT=Saturn your-email@example.com   # required contact UA for SEC EDGAR
```

Note: `test_new_keys_read_from_env` overrides the autouse fixture's `delenv` by calling `monkeypatch.setenv` afterward in the test body — monkeypatch applies in order, and the test's own setenv runs after the fixture, so the values are present. This is intended.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/config.py tests/conftest.py tests/test_config.py .env.example
git commit -m "feat(config): add FRED_API_KEY and SEC_USER_AGENT settings"
```

---

## Task 4: Per-source TTL disk cache in `saturn/ingestion/cache.py`

**Files:**
- Create: `saturn/ingestion/cache.py`
- Modify: `.gitignore`
- Test: `tests/ingestion/test_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_cache.py`:

```python
from datetime import date

from saturn.ingestion.cache import read_cache, write_cache


def test_write_then_read_roundtrip(tmp_path):
    payload = {"hello": "world", "n": 1}
    write_cache("edgar", "NVDA", payload, root=tmp_path, today=date(2026, 6, 6))
    got = read_cache(
        "edgar", "NVDA", ttl_days=30, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got == payload


def test_miss_returns_none(tmp_path):
    got = read_cache("edgar", "MSFT", ttl_days=30, root=tmp_path, today=date(2026, 6, 6))
    assert got is None


def test_expired_entry_is_a_miss(tmp_path):
    write_cache("fred", "MACRO", {"x": 1}, root=tmp_path, today=date(2026, 6, 1))
    # 5 days later with a 1-day TTL -> expired.
    got = read_cache(
        "fred", "MACRO", ttl_days=1, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got is None


def test_fresh_entry_within_ttl_hits(tmp_path):
    write_cache("fred", "MACRO", {"x": 1}, root=tmp_path, today=date(2026, 6, 6))
    got = read_cache(
        "fred", "MACRO", ttl_days=1, root=tmp_path, today=date(2026, 6, 6)
    )
    assert got == {"x": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.cache'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/cache.py`:

```python
"""Tiny per-source disk cache for ingestion payloads.

Entries are JSON files under `<root>/<source>/<key>_<YYYY-MM-DD>.json`. A read is
a hit only if a file exists whose date stamp is within `ttl_days` of `today`.
The newest in-window file wins. Dates are injected (never read from the clock
here) so the behaviour is deterministic and testable.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path("data/cache")


def _dir(source: str, root: Path) -> Path:
    return root / source


def write_cache(
    source: str,
    key: str,
    payload: object,
    *,
    root: Path = DEFAULT_ROOT,
    today: date,
) -> Path:
    """Write `payload` as JSON and return the path written."""
    d = _dir(source, root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}_{today:%Y-%m-%d}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("cache write: %s", path)
    return path


def read_cache(
    source: str,
    key: str,
    *,
    ttl_days: int,
    root: Path = DEFAULT_ROOT,
    today: date,
) -> object | None:
    """Return the freshest cached payload within TTL, or None on miss."""
    d = _dir(source, root)
    if not d.exists():
        return None
    best_date: date | None = None
    best_path: Path | None = None
    prefix = f"{key}_"
    for path in d.glob(f"{key}_*.json"):
        stamp = path.stem[len(prefix):]
        try:
            stamp_date = date.fromisoformat(stamp)
        except ValueError:
            continue
        age = (today - stamp_date).days
        if 0 <= age <= ttl_days and (best_date is None or stamp_date > best_date):
            best_date, best_path = stamp_date, path
    if best_path is None:
        return None
    logger.info("cache hit: %s", best_path)
    return json.loads(best_path.read_text(encoding="utf-8"))
```

In `.gitignore`, add a line after `reports/*` block:

```text
data/cache/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_cache.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/cache.py .gitignore tests/ingestion/test_cache.py
git commit -m "feat(ingestion): add per-source TTL disk cache"
```

---

## Task 5: Quote adapter (yfinance) returning canonical `Quote`

**Files:**
- Modify: `saturn/ingestion/prices.py` (add `fetch_quote`)
- Test: `tests/ingestion/test_quote.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_quote.py`:

```python
from saturn.ingestion.prices import _mock_quote, fetch_quote
from saturn.models import Quote


def test_mock_quote_shape():
    q = _mock_quote("NVDA")
    assert isinstance(q, Quote)
    assert q.price is not None
    assert q.provenance.source == "yfinance (mock)"


def test_fetch_quote_mock_path():
    q = fetch_quote("ANYTHING", mock=True)
    assert isinstance(q, Quote)
    assert q.currency == "USD"
    assert q.provenance.source == "yfinance (mock)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_quote.py -v`
Expected: FAIL with `ImportError: cannot import name '_mock_quote'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/ingestion/prices.py`, add these imports at the top (extend the existing `from saturn.models import ...`):

```python
from datetime import date

from saturn.models import CompanyData, NewsItem, Provenance, Quote
from saturn.ingestion.errors import IngestionError, SourceFailure
```

Add near the bottom of the file:

```python
def _mock_quote(ticker: str) -> Quote:
    return Quote(
        price=900.0,
        market_cap=2_200_000_000_000.0,
        currency="USD",
        provenance=Provenance(source="yfinance (mock)", as_of=date.today()),
    )


def fetch_quote(ticker: str, *, mock: bool = False) -> Quote:
    """Return a canonical Quote for `ticker`. mock=True for offline fixture."""
    if mock:
        logger.info("quote(mock): %s", ticker)
        return _mock_quote(ticker)

    logger.info("quote(yfinance): %s", ticker)
    try:
        import yfinance as yf

        info = (yf.Ticker(ticker).info) or {}
    except Exception as exc:  # noqa: BLE001 - surface as a typed error
        raise SourceFailure(f"yfinance quote failed for {ticker}") from exc

    return Quote(
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        market_cap=info.get("marketCap"),
        currency=info.get("currency"),
        provenance=Provenance(
            source="yfinance",
            source_url=f"https://finance.yahoo.com/quote/{ticker}",
            retrieved_at=date.today(),
        ),
    )
```

(Leave the existing `fetch_company_data`/`_mock_company`/`_extract_news` intact; they are removed in a later cleanup once nothing imports them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_quote.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/prices.py tests/ingestion/test_quote.py
git commit -m "feat(ingestion): add canonical Quote adapter over yfinance"
```

---

## Task 6: Soft-fail dispatcher in `saturn/ingestion/dispatch.py`

**Files:**
- Create: `saturn/ingestion/dispatch.py`
- Test: `tests/ingestion/test_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_dispatch.py`:

```python
from saturn.ingestion.dispatch import route_to_source
from saturn.ingestion.errors import DataUnavailable, SourceFailure
from saturn.models import SourceGap


def test_success_returns_value_and_no_gap():
    result, gap = route_to_source("edgar", lambda: {"ok": 1})
    assert result == {"ok": 1}
    assert gap is None


def test_data_unavailable_becomes_gap():
    def boom():
        raise DataUnavailable("no CIK for ZZZZ")

    result, gap = route_to_source("edgar", boom)
    assert result is None
    assert isinstance(gap, SourceGap)
    assert gap.source == "edgar"
    assert "no CIK" in gap.reason


def test_source_failure_becomes_gap():
    def boom():
        raise SourceFailure("connection reset")

    result, gap = route_to_source("fred", boom)
    assert result is None
    assert gap.source == "fred"
    assert "connection reset" in gap.reason


def test_unexpected_error_also_becomes_gap():
    def boom():
        raise ValueError("surprise")

    result, gap = route_to_source("fred", boom)
    assert result is None
    assert gap.source == "fred"
    assert "surprise" in gap.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.dispatch'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/dispatch.py`:

```python
"""Soft-fail dispatch for ingestion sources.

A source is just a zero-arg callable that returns a canonical object or raises.
`route_to_source` converts any failure into a recorded `SourceGap` so a single
flaky source never crashes the whole dossier — adopted from TradingAgents'
route_to_vendor pattern, adapted to a (result, gap) return.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from saturn.ingestion.errors import IngestionError
from saturn.models import SourceGap

logger = logging.getLogger(__name__)

T = TypeVar("T")


def route_to_source(
    source: str, fetch: Callable[[], T]
) -> tuple[T | None, SourceGap | None]:
    """Call `fetch`; return (result, None) on success or (None, gap) on failure."""
    try:
        return fetch(), None
    except IngestionError as exc:
        logger.warning("source %s unavailable: %s", source, exc)
        return None, SourceGap(source=source, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let one source crash the run
        logger.warning("source %s errored: %s", source, exc)
        return None, SourceGap(source=source, reason=f"{type(exc).__name__}: {exc}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dispatch.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dispatch.py tests/ingestion/test_dispatch.py
git commit -m "feat(ingestion): add soft-fail route_to_source dispatcher"
```

---

## Task 7: Dossier orchestration in `saturn/ingestion/dossier.py`

**Files:**
- Create: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py`

This task wires the quote adapter for real and registers `edgar`/`fred` as not-yet-available so they degrade to gaps until their plans land. EDGAR/FRED hooks are module-level names (`_edgar_source`, `_fred_source`) the later plans will replace.

- [ ] **Step 1: Write the failing test**

Create `tests/ingestion/test_dossier.py`:

```python
from saturn.ingestion.dossier import _mock_dossier, build_dossier
from saturn.models import CompanyDossier


def test_mock_dossier_is_rich():
    d = _mock_dossier("NVDA")
    assert isinstance(d, CompanyDossier)
    assert d.quote is not None and d.quote.price is not None
    assert d.fundamentals is not None and len(d.fundamentals.facts) >= 1
    assert d.macro is not None and len(d.macro.series) >= 1
    assert d.filing_sections and d.filing_sections[0].name
    # every datum is provenance-tagged
    assert d.quote.provenance.source
    assert d.fundamentals.facts[0].provenance.source
    assert d.macro.series[0].provenance.source


def test_build_dossier_mock_path_returns_mock():
    d = build_dossier("NVDA", mock=True)
    assert d.ticker == "NVDA"
    assert d.quote is not None


def test_build_dossier_real_path_quote_only_records_gaps():
    # Real path with quote stubbed to succeed and edgar/fred unavailable.
    from saturn.models import Provenance, Quote

    def fake_quote(ticker, *, mock):
        return Quote(price=1.0, currency="USD", provenance=Provenance(source="yfinance"))

    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=fake_quote,
        edgar_fn=None,   # not wired yet
        fred_fn=None,    # not wired yet
        identity={"name": "NVIDIA Corporation"},
    )
    assert d.quote.price == 1.0
    assert d.fundamentals is None
    gap_sources = {g.source for g in d.gaps}
    assert "edgar" in gap_sources and "fred" in gap_sources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.dossier'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/dossier.py`:

```python
"""Assemble a CompanyDossier from source adapters via the dispatcher.

Slice-1 framework: the quote adapter (yfinance) is wired for real. EDGAR and
FRED are passed in as optional callables; until their plans land they default to
None and the dispatcher records a gap. This keeps the orchestration shape stable
while real adapters are added incrementally.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from saturn.ingestion.dispatch import route_to_source
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.prices import fetch_quote
from saturn.models import (
    CompanyDossier,
    FilingSection,
    FinancialFact,
    Fundamentals,
    MacroSeries,
    MacroSnapshot,
    NewsItem,
    Provenance,
    Quote,
)

logger = logging.getLogger(__name__)


def _mock_dossier(ticker: str) -> CompanyDossier:
    prov_q = Provenance(source="yfinance (mock)", as_of=date.today())
    prov_e = Provenance(
        source="SEC EDGAR (mock)",
        source_url="https://www.sec.gov/",
        as_of=date(2025, 2, 21),
    )
    prov_f = Provenance(source="FRED (mock)", as_of=date(2026, 5, 1))
    return CompanyDossier(
        ticker=ticker,
        cik="0001045810",
        name="NVIDIA Corporation",
        sector="Technology",
        industry="Semiconductors",
        business_summary="[MOCK] Designs GPUs and accelerated computing platforms.",
        segments=["Data Center", "Gaming", "Professional Visualization", "Automotive"],
        quote=Quote(price=900.0, market_cap=2_200_000_000_000.0, currency="USD", provenance=prov_q),
        fundamentals=Fundamentals(
            facts=[
                FinancialFact(concept="Revenues", value=60_900_000_000.0, unit="USD", fiscal_period="FY2024", provenance=prov_e),
                FinancialFact(concept="NetIncomeLoss", value=29_760_000_000.0, unit="USD", fiscal_period="FY2024", provenance=prov_e),
                FinancialFact(concept="Revenues", value=26_970_000_000.0, unit="USD", fiscal_period="FY2023", provenance=prov_e),
            ]
        ),
        filing_sections=[
            FilingSection(
                name="Risk Factors",
                excerpt="[MOCK] Demand for our products may not meet expectations; supply is concentrated.",
                provenance=prov_e,
            )
        ],
        macro=MacroSnapshot(
            series=[
                MacroSeries(
                    series_id="FEDFUNDS",
                    title="Federal Funds Effective Rate",
                    observations=[(date(2026, 4, 1), 4.33)],
                    provenance=prov_f,
                )
            ]
        ),
        news=[NewsItem(title="[MOCK] NVIDIA announces next-gen architecture", publisher="MockWire", link="https://example.com/mock")],
        generated_at=date.today(),
    )


def build_dossier(
    ticker: str,
    *,
    mock: bool = False,
    quote_fn: Callable[..., Quote] = fetch_quote,
    edgar_fn: Callable[..., object] | None = None,
    fred_fn: Callable[..., object] | None = None,
    identity: dict | None = None,
) -> CompanyDossier:
    """Build a CompanyDossier. mock=True returns the offline fixture.

    edgar_fn/fred_fn are injected by later plans; when None, the dispatcher
    records a gap for that source.
    """
    if mock:
        logger.info("dossier(mock): %s", ticker)
        return _mock_dossier(ticker)

    ident = identity or {}
    gaps = []

    quote, gap = route_to_source("quote", lambda: quote_fn(ticker, mock=False))
    if gap:
        gaps.append(gap)

    def _edgar():
        if edgar_fn is None:
            raise DataUnavailable("edgar adapter not configured")
        return edgar_fn(ticker)

    edgar_result, gap = route_to_source("edgar", _edgar)
    if gap:
        gaps.append(gap)

    def _fred():
        if fred_fn is None:
            raise DataUnavailable("fred adapter not configured")
        return fred_fn(ticker)

    fred_result, gap = route_to_source("fred", _fred)
    if gap:
        gaps.append(gap)

    fundamentals = filing_sections = None
    if isinstance(edgar_result, dict):
        fundamentals = edgar_result.get("fundamentals")
        filing_sections = edgar_result.get("filing_sections")

    return CompanyDossier(
        ticker=ticker,
        cik=ident.get("cik"),
        name=ident.get("name", ticker),
        sector=ident.get("sector"),
        industry=ident.get("industry"),
        business_summary=ident.get("business_summary"),
        segments=ident.get("segments", []),
        quote=quote,
        fundamentals=fundamentals,
        filing_sections=filing_sections or [],
        macro=fred_result if isinstance(fred_result, MacroSnapshot) else None,
        news=ident.get("news", []),
        gaps=gaps,
        generated_at=date.today(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(ingestion): assemble CompanyDossier via soft-fail dispatch"
```

---

## Task 8: Pipeline operates on `CompanyDossier` with inline provenance

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py` (extend existing; if absent, create)

This is the F1 fix: `_company_context` renders the dossier as provenance-tagged text so the model — and a future Critic — can cite sources.

- [ ] **Step 1: Write the failing test**

Create/extend `tests/test_equity_research.py`:

```python
from datetime import date

from saturn.ingestion.dossier import _mock_dossier
from saturn.llm.mock_client import MockLLMClient
from saturn.workflows.equity_research import _company_context, run


def test_company_context_includes_inline_provenance():
    ctx = _company_context(_mock_dossier("NVDA"))
    assert "NVIDIA Corporation" in ctx
    # financial facts are rendered with their source
    assert "Revenues" in ctx
    assert "SEC EDGAR (mock)" in ctx
    # macro present with source
    assert "Federal Funds" in ctx


def test_run_accepts_dossier_and_builds_report():
    dossier = _mock_dossier("NVDA")
    report = run(dossier, MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.company.quote.price == 900.0
    assert report.analysis.executive_summary
    assert report.debate.bull_thesis
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -v`
Expected: FAIL — `_company_context` currently takes `CompanyData` and calls `.model_dump_json`; the dossier has no `metrics`/`news` shape match, and `run` typing/`_build_sources` reference `company.news`/`CompanyData`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/workflows/equity_research.py`, change the import block:

```python
from saturn.models import (
    AnalysisSections,
    CompanyDossier,
    DebateSections,
    ResearchReport,
)
```

Replace `_company_context` with a provenance-rendering version:

```python
def _company_context(dossier: CompanyDossier) -> str:
    """Render the dossier as provenance-tagged text the agents can cite."""
    lines: list[str] = []
    lines.append(f"COMPANY: {dossier.name} ({dossier.ticker})")
    if dossier.cik:
        lines.append(f"CIK: {dossier.cik}")
    for label, val in (("Sector", dossier.sector), ("Industry", dossier.industry)):
        if val:
            lines.append(f"{label}: {val}")
    if dossier.business_summary:
        lines.append(f"Business summary: {dossier.business_summary}")
    if dossier.segments:
        lines.append(f"Segments: {', '.join(dossier.segments)}")

    if dossier.quote:
        q = dossier.quote
        lines.append(
            f"\nQUOTE (source: {q.provenance.source}): "
            f"price={q.price} {q.currency or ''}, market_cap={q.market_cap}"
        )

    if dossier.fundamentals and dossier.fundamentals.facts:
        lines.append("\nFUNDAMENTALS (as-reported):")
        for f in dossier.fundamentals.facts:
            cite = f.provenance.source
            if f.provenance.as_of:
                cite += f", as of {f.provenance.as_of}"
            period = f.fiscal_period or "?"
            lines.append(
                f"- {f.concept} {period}: {f.value} {f.unit or ''} (source: {cite})"
            )

    if dossier.filing_sections:
        lines.append("\nFILING SECTIONS:")
        for s in dossier.filing_sections:
            lines.append(f"- {s.name} (source: {s.provenance.source}): {s.excerpt}")

    if dossier.macro and dossier.macro.series:
        lines.append("\nMACRO:")
        for m in dossier.macro.series:
            latest = m.observations[-1] if m.observations else None
            val = f"{latest[1]} (as of {latest[0]})" if latest else "n/a"
            lines.append(f"- {m.title} [{m.series_id}]: {val} (source: {m.provenance.source})")

    if dossier.news:
        lines.append("\nNEWS:")
        for n in dossier.news:
            lines.append(f"- {n.title}" + (f" — {n.publisher}" if n.publisher else ""))

    if dossier.gaps:
        lines.append("\nDATA GAPS (sources unavailable this run):")
        for g in dossier.gaps:
            lines.append(f"- {g.source}: {g.reason}")

    return "\n".join(lines)
```

Update `analyze` and `debate` signatures to take `CompanyDossier` (only the type annotation changes; the bodies already call `_company_context(company)`):

```python
def analyze(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> AnalysisSections:
```

```python
def debate(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> DebateSections:
```

Replace `_build_sources` and `run` to use the dossier:

```python
def _build_sources(dossier: CompanyDossier, *, mock: bool) -> list[str]:
    if mock:
        return ["MOCK fixture data — not real market sources"]
    sources: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        if label and label not in seen:
            seen.add(label)
            sources.append(label)

    if dossier.quote:
        _add(dossier.quote.provenance.source)
    if dossier.fundamentals:
        for f in dossier.fundamentals.facts:
            url = f.provenance.source_url
            _add(url or f.provenance.source)
    for s in dossier.filing_sections:
        _add(s.provenance.source_url or s.provenance.source)
    if dossier.macro:
        for m in dossier.macro.series:
            _add(m.provenance.source)
    for n in dossier.news:
        if n.link:
            _add(n.link)
    for g in dossier.gaps:
        _add(f"(gap) {g.source}: {g.reason}")
    return sources


def run(
    company: CompanyDossier,
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

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): run pipeline on CompanyDossier with inline-provenance context"
```

---

## Task 9: Report renderer + CLI on `CompanyDossier`

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Modify: `saturn/cli.py`
- Test: `tests/test_markdown_report.py` (extend/create), `tests/test_cli.py` (extend/create)

- [ ] **Step 1: Write the failing test**

Create/extend `tests/test_markdown_report.py`:

```python
from datetime import date

from saturn.ingestion.dossier import _mock_dossier
from saturn.models import (
    AnalysisSections,
    DebateSections,
    ResearchReport,
)
from saturn.reports.markdown_report import render


def _report():
    return ResearchReport(
        ticker="NVDA",
        company=_mock_dossier("NVDA"),
        analysis=AnalysisSections(
            executive_summary="es", company_overview="co", business_segments="bs",
            financial_snapshot="fs", valuation_discussion="vd", key_risks="kr",
            open_questions="oq",
        ),
        debate=DebateSections(bull_thesis="bull", bear_thesis="bear", final_view="fv"),
        generated_at=date(2026, 6, 6),
        model_used="mock",
        mock=True,
        sources=["MOCK fixture data — not real market sources"],
    )


def test_render_includes_quote_and_financials_table():
    md = render(_report())
    assert "# NVDA Equity Research Report" in md
    assert "$900" in md  # quote price humanized
    assert "Revenues" in md  # fundamentals table
    assert "FY2024" in md
    assert "Federal Funds Effective Rate" in md  # macro snapshot
    assert "not investment advice" in md
```

Create/extend `tests/test_cli.py` (mirror the existing CLI test that asserts the `--mock` path writes a file; if one exists, just confirm it still passes after the change — no new test needed beyond the smoke run in Task 10).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_markdown_report.py -v`
Expected: FAIL — `render` reads `c.metrics`/`c.price` off `CompanyData`; the dossier exposes `c.quote`/`c.fundamentals` instead.

- [ ] **Step 3: Write minimal implementation**

In `saturn/reports/markdown_report.py`, replace the body of `render` sections 4–6 and add a macro section. Full new file:

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
    if c.quote:
        out.append(f"- Price: {_fmt_money(c.quote.price)} {c.quote.currency or ''}".rstrip())
        out.append(f"- Market cap: {_fmt_money(c.quote.market_cap)}")
        out.append(f"- _Source: {c.quote.provenance.source}_")
    else:
        out.append("_No quote available._")
    out.append("")

    out += ["## 5. Financial Snapshot", ""]
    if c.fundamentals and c.fundamentals.facts:
        out.append("| Concept | Period | Value | Unit | Source |")
        out.append("| --- | --- | --- | --- | --- |")
        for f in c.fundamentals.facts:
            val = _fmt_money(f.value) if (f.unit or "").upper() == "USD" else (
                f.value if f.value is not None else "N/A"
            )
            out.append(
                f"| {f.concept} | {f.fiscal_period or 'N/A'} | {val} "
                f"| {f.unit or ''} | {f.provenance.source} |"
            )
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

    out += ["## 13. Macro Snapshot", ""]
    if c.macro and c.macro.series:
        out.append("| Series | Latest | As of | Source |")
        out.append("| --- | --- | --- | --- |")
        for m in c.macro.series:
            latest = m.observations[-1] if m.observations else None
            val = latest[1] if latest else "N/A"
            asof = latest[0] if latest else "N/A"
            out.append(f"| {m.title} | {val} | {asof} | {m.provenance.source} |")
        out.append("")
    else:
        out.append("_No macro data available._")
        out.append("")

    out += ["## 14. Sources", ""]
    if report.sources:
        out += [f"- {s}" for s in report.sources]
    else:
        out.append("_No sources recorded._")
    out.append("")

    if c.gaps:
        out += ["## 15. Data Gaps", ""]
        out += [f"- **{g.source}**: {g.reason}" for g in c.gaps]
        out.append("")

    out += ["---", "", _DISCLAIMER, ""]
    return "\n".join(out)
```

In `saturn/cli.py`, change the ingestion import and call. Replace:

```python
from saturn.ingestion.prices import IngestionError, fetch_company_data
```

with:

```python
from saturn.ingestion.dossier import build_dossier
from saturn.ingestion.errors import IngestionError
```

and replace the `try: company = fetch_company_data(...)` block:

```python
    try:
        company = build_dossier(ticker, mock=mock)
    except IngestionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
```

(`build_dossier` itself soft-fails per source, so the `except` mainly guards the mock path and any future hard failure; keep it for safety.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_markdown_report.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py saturn/cli.py tests/test_markdown_report.py tests/test_cli.py
git commit -m "feat(report,cli): render CompanyDossier (financials table, macro, gaps)"
```

---

## Task 10: Full-suite + end-to-end mock verification

**Files:**
- No new code; verification + any fix-ups discovered.

- [ ] **Step 1: Run the whole suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass (Phase-0 tests + the new ingestion/model/workflow/report tests). If a Phase-0 test still references the old `CompanyData` report shape (e.g. a fixture building `ResearchReport(company=CompanyData(...))`), update that fixture to use `_mock_dossier(...)` from `saturn.ingestion.dossier`, then re-run.

- [ ] **Step 2: End-to-end offline smoke run**

Run: `.venv\Scripts\python.exe -m saturn.cli research NVDA --mock`
Expected: prints `[MOCK MODE] Wrote reports\NVDA_<DATE>.md`.

- [ ] **Step 3: Inspect the report**

Open `reports/NVDA_<DATE>.md` and confirm: a Financial Snapshot table with `Revenues`/`FY2024`/source column, a Macro Snapshot table, the quote source line, and the disclaimer. (No Data Gaps section in mock mode, since the mock dossier records none.)

- [ ] **Step 4: Real-path gap check (no keys, offline-safe)**

Run: `.venv\Scripts\python.exe -c "from saturn.ingestion.dossier import build_dossier; d = build_dossier('NVDA', mock=False, quote_fn=lambda t, *, mock: __import__('saturn.models', fromlist=['Quote','Provenance']).Quote(price=1.0, provenance=__import__('saturn.models', fromlist=['Provenance']).Provenance(source='stub')) , edgar_fn=None, fred_fn=None); print([g.source for g in d.gaps])"`
Expected: prints `['edgar', 'fred']` — confirming soft-fail gaps without touching the network.

- [ ] **Step 5: Commit any fix-ups**

```bash
git add -A
git commit -m "test: update fixtures for CompanyDossier; verify enriched pipeline end-to-end"
```

---

## Self-Review

**Spec coverage (against `2026-05-31-data-ingestion-enrichment-design.md`):**
- §2 canonical model → Task 1 ✓
- §5b typed errors → Task 2 ✓
- config keys (`FRED_API_KEY`, `SEC_USER_AGENT`) → Task 3 ✓
- caching (per-source TTL, raw/canonical JSON) → Task 4 ✓
- yfinance quote-only adapter → Task 5 ✓
- §5a dispatcher + soft-fail → Task 6 ✓
- §1/§4 dossier orchestration + integration → Tasks 7–9 ✓
- §4 inline-provenance rendering (F1 fix) → Task 8 ✓
- §6 offline testing (fixtures, autouse env guard) → all tasks; Task 10 verifies ✓
- **Deferred to follow-on plans (declared in scope note):** §3 EDGAR adapter (companyfacts + 10-K sections), §3 FRED adapter, §3a `identifiers.py` (ticker→CIK, FRED series registry). These implement `edgar_fn`/`fred_fn` injected into `build_dossier`. Gap is intentional and documented, not an omission.

**Placeholder scan:** No TBD/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✓

**Type consistency:** `CompanyDossier`, `Quote`, `Fundamentals.facts`, `FinancialFact.{concept,value,unit,fiscal_period,provenance}`, `MacroSnapshot.series`, `MacroSeries.{series_id,title,observations,provenance}`, `SourceGap.{source,reason}`, `Provenance.{source,source_url,as_of,retrieved_at}` are defined in Task 1 and used identically in Tasks 7–9. `route_to_source(source, fetch) -> (result, gap)` defined in Task 6, used in Task 7. `build_dossier(ticker, *, mock, quote_fn, edgar_fn, fred_fn, identity)` defined in Task 7, called in Task 9. `fetch_quote(ticker, *, mock)` defined in Task 5, used as default in Task 7. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-enrichment-framework.md`.

Follow-on plans to write next (the EDGAR ‖ FRED parallel pair): `2026-06-06-edgar-adapter.md` and `2026-06-06-fred-adapter.md`, each implementing one injected source function plus the `identifiers.py` piece it needs, with offline fixture-based tests.

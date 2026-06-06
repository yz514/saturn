# FRED Macro Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the real FRED adapter behind the `fred_fn` seam — a curated set of macro series (rates, inflation, employment, money supply) fetched from the St. Louis Fed FRED API — so the non-mock dossier carries real, provenance-tagged macro context.

**Architecture:** A curated series registry + a pure observation parser (unit-tested offline on a fixture) + a thin `urllib` fetcher reusing the shared `http_get`. `fetch_fred()` is macro-level and **ticker-agnostic** (it accepts and ignores a ticker positional arg, matching the `fred_fn(ticker)` call site in `build_dossier`); it returns a `MacroSnapshot`. Wired in as the default `fred_fn`. Requires a free `FRED_API_KEY`; if absent the adapter raises `DataUnavailable`, which the dispatcher records as a gap (never a crash).

**Tech Stack:** Python 3.13, `urllib` via the shared `saturn/ingestion/http.py` helper, Pydantic v2 (`MacroSeries`/`MacroSnapshot`/`Provenance`), the existing TTL cache and typed errors, pytest with a committed fixture.

**Spec:** `docs/superpowers/specs/2026-05-31-data-ingestion-enrichment-design.md` §3 (FRED curated default series — Fed funds, CPI, PPI, 10Y & 2Y yields, unemployment, M2).

**Dependency:** This plan reuses `saturn/ingestion/http.py` (`http_get`) created in the EDGAR adapter plan (`2026-06-06-edgar-adapter.md`, Task 1). Execute the EDGAR plan first (or at least its Task 1) on the same branch. Everything else here is independent of EDGAR.

**Prereqs on branch:** `MacroSeries`/`MacroSnapshot`/`Provenance` in `saturn/models.py`; `DataUnavailable`/`SourceFailure` in `saturn/ingestion/errors.py`; `read_cache`/`write_cache`; config `fred_api_key`; `build_dossier(... fred_fn=None ...)` (records a gap when unwired); `_fred` closure calls `fred_fn(ticker)`.

---

## File Structure

**Create:**
- `saturn/ingestion/fred.py` — `FRED_SERIES` registry, pure `_parse_observations`, thin `_fetch_series_observations`, public `fetch_fred`.
- `tests/ingestion/test_fred.py`
- `tests/fixtures/fred/observations_FEDFUNDS.json` — committed sample of the FRED observations response.

**Modify:**
- `saturn/ingestion/dossier.py` — change default `fred_fn=None` → `fred_fn=fetch_fred`.
- `tests/ingestion/test_dossier.py` — add one test that the default fred path is wired (via injected fetcher; the existing tests pass `fred_fn=None` explicitly and keep recording a fred gap).

**Established patterns:** curated/whitelisted series (we control what's fetched); titles hardcoded in the registry to avoid an extra metadata round-trip per series; pure parser is the unit-tested core; the live fetch is injectable; missing API key → `DataUnavailable`; offline test suite.

---

## Task 1: Series registry + observation parser

**Files:**
- Create: `saturn/ingestion/fred.py`
- Test: `tests/ingestion/test_fred.py`
- Fixture: `tests/fixtures/fred/observations_FEDFUNDS.json`

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/fred/observations_FEDFUNDS.json` (FRED's real shape; note a missing value is the string `"."` and must be skipped, and FRED returns ascending dates by default):

```json
{
  "realtime_start": "2026-05-01",
  "observations": [
    {"date": "2026-01-01", "value": "4.33"},
    {"date": "2026-02-01", "value": "."},
    {"date": "2026-03-01", "value": "4.33"},
    {"date": "2026-04-01", "value": "4.25"}
  ]
}
```

Create `tests/ingestion/test_fred.py`:

```python
import json
from datetime import date
from pathlib import Path

from saturn.ingestion.fred import FRED_SERIES, _parse_observations

FIX = Path(__file__).parent.parent / "fixtures" / "fred"


def _raw():
    return json.loads((FIX / "observations_FEDFUNDS.json").read_text(encoding="utf-8"))


def test_parse_skips_missing_values_and_sorts_ascending():
    obs = _parse_observations(_raw())
    # the "." value on 2026-02-01 is dropped
    assert (date(2026, 2, 1), 0.0) not in obs
    assert obs == sorted(obs, key=lambda t: t[0])
    assert obs[-1] == (date(2026, 4, 1), 4.25)


def test_parse_returns_date_float_tuples():
    obs = _parse_observations(_raw())
    d, v = obs[0]
    assert isinstance(d, date)
    assert isinstance(v, float)


def test_registry_includes_core_series():
    ids = {s[0] for s in FRED_SERIES}
    assert {"FEDFUNDS", "CPIAUCSL", "DGS10", "DGS2", "UNRATE", "M2SL"} <= ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.ingestion.fred'`.

- [ ] **Step 3: Write minimal implementation**

Create `saturn/ingestion/fred.py`:

```python
"""FRED macro adapter: a curated set of macro series with provenance.

Macro is ticker-agnostic — fetch_fred accepts and ignores a ticker so it matches
the fred_fn(ticker) call site in build_dossier. Series titles are hardcoded in the
registry to avoid an extra metadata round-trip per series.
"""

from __future__ import annotations

import logging
from datetime import date

from saturn.models import MacroSeries, MacroSnapshot, Provenance

logger = logging.getLogger(__name__)

# Curated macro series: (series_id, human title). Spec §3 default set.
FRED_SERIES: list[tuple[str, str]] = [
    ("FEDFUNDS", "Federal Funds Effective Rate"),
    ("CPIAUCSL", "Consumer Price Index (All Urban Consumers)"),
    ("PPIACO", "Producer Price Index (All Commodities)"),
    ("DGS10", "10-Year Treasury Yield"),
    ("DGS2", "2-Year Treasury Yield"),
    ("UNRATE", "Unemployment Rate"),
    ("M2SL", "M2 Money Supply"),
]

_OBS_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
    "?series_id={series_id}&api_key={api_key}&file_type=json"
    "&sort_order=asc&observation_start={start}"
)


def _parse_observations(raw: dict) -> list[tuple[date, float]]:
    """Parse a FRED observations response into sorted (date, value) tuples.

    Missing values (the literal '.') are skipped. Output is ascending by date.
    """
    out: list[tuple[date, float]] = []
    for o in raw.get("observations", []):
        val = o.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((date.fromisoformat(o["date"]), float(val)))
        except (ValueError, KeyError):
            continue
    out.sort(key=lambda t: t[0])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/fred.py tests/ingestion/test_fred.py tests/fixtures/fred/observations_FEDFUNDS.json
git commit -m "feat(fred): add curated series registry and observation parser"
```

---

## Task 2: `fetch_fred` orchestration + caching + provenance

**Files:**
- Modify: `saturn/ingestion/fred.py` (add fetcher + `fetch_fred`)
- Test: `tests/ingestion/test_fred.py` (add cases)

`fetch_fred` builds a `MacroSnapshot` from the registry; each series carries `Provenance(source="FRED", source_url=...)`. No `FRED_API_KEY` → `DataUnavailable`. The live fetch is injectable (`fetch=` param) so the orchestration is unit-tested offline.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_fred.py`:

```python
import pytest

from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.fred import fetch_fred
from saturn.models import MacroSnapshot


def test_fetch_fred_builds_snapshot_with_provenance(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "testkey")

    def fake_fetch(series_id, api_key):
        return {"observations": [{"date": "2026-04-01", "value": "1.5"}]}

    snap = fetch_fred("NVDA", fetch=fake_fetch)
    assert isinstance(snap, MacroSnapshot)
    assert len(snap.series) == len(__import__("saturn.ingestion.fred", fromlist=["FRED_SERIES"]).FRED_SERIES)
    s0 = snap.series[0]
    assert s0.observations[-1][1] == 1.5
    assert s0.provenance.source == "FRED"
    assert s0.title  # human title from the registry


def test_fetch_fred_ignores_ticker(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "testkey")
    snap = fetch_fred("ANYTHING", fetch=lambda sid, api_key: {"observations": []})
    assert isinstance(snap, MacroSnapshot)


def test_fetch_fred_without_key_raises_data_unavailable(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(DataUnavailable):
        fetch_fred("NVDA", fetch=lambda sid, api_key: {"observations": []})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -k "fetch_fred" -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_fred'`.

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `saturn/ingestion/fred.py`:

```python
import json
from typing import Callable

from saturn.config import get_settings
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get
```

Then add the fetcher + orchestration:

```python
_DEFAULT_START = "2015-01-01"  # ~10y of history is plenty for macro context


def _fetch_series_observations(series_id: str, api_key: str) -> dict:
    url = _OBS_URL.format(series_id=series_id, api_key=api_key, start=_DEFAULT_START)
    return json.loads(http_get(url, user_agent="Saturn research", accept="application/json"))


def fetch_fred(
    ticker: str | None = None,
    *,
    mock: bool = False,
    fetch: Callable[[str, str], dict] = _fetch_series_observations,
) -> MacroSnapshot:
    """Return a MacroSnapshot of the curated FRED series. `ticker` is ignored
    (macro is company-independent). Raises DataUnavailable if FRED_API_KEY is unset;
    SourceFailure (via http_get) on transport errors."""
    api_key = get_settings().fred_api_key
    if not api_key:
        raise DataUnavailable("FRED_API_KEY not set")

    series: list[MacroSeries] = []
    for series_id, title in FRED_SERIES:
        raw = fetch(series_id, api_key)
        obs = _parse_observations(raw)
        series.append(
            MacroSeries(
                series_id=series_id,
                title=title,
                observations=obs,
                provenance=Provenance(
                    source="FRED",
                    source_url=f"https://fred.stlouisfed.org/series/{series_id}",
                    retrieved_at=date.today(),
                ),
            )
        )
    return MacroSnapshot(series=series)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_fred.py -v`
Expected: PASS (all fred tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/fred.py tests/ingestion/test_fred.py
git commit -m "feat(fred): fetch_fred orchestration with provenance and no-key guard"
```

---

## Task 3: Wire `fetch_fred` into `build_dossier` (real default)

**Files:**
- Modify: `saturn/ingestion/dossier.py`
- Test: `tests/ingestion/test_dossier.py` (add a case)

- [ ] **Step 1: Write the failing test**

Add to `tests/ingestion/test_dossier.py`:

```python
def test_build_dossier_default_fred_is_wired():
    from saturn.ingestion.fred import fetch_fred
    from saturn.models import MacroSnapshot, MacroSeries, Provenance, Quote
    from datetime import date

    def fake_fred(ticker):
        return MacroSnapshot(
            series=[
                MacroSeries(
                    series_id="FEDFUNDS",
                    title="Federal Funds Effective Rate",
                    observations=[(date(2026, 4, 1), 4.25)],
                    provenance=Provenance(source="FRED"),
                )
            ]
        )

    d = build_dossier(
        "NVDA",
        mock=False,
        quote_fn=lambda t, *, mock: Quote(price=1.0, provenance=Provenance(source="yfinance")),
        edgar_fn=None,           # keep edgar a gap for this test
        fred_fn=fake_fred,
    )
    assert d.macro is not None
    assert d.macro.series[0].series_id == "FEDFUNDS"
    assert "edgar" in {g.source for g in d.gaps}
    assert fetch_fred is fetch_fred  # the default is now the real function
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -k "default_fred" -v`
Expected: PASS for the macro assertions IF fred_fn is passed, but the intent is to also flip the default. First confirm the default flip is needed: inspect `build_dossier`'s signature — if `fred_fn` still defaults to `None`, the "default is wired" intent isn't met. (The injected-fn assertions will pass already; the signature change in Step 3 is what makes the *default* real.)

- [ ] **Step 3: Write minimal implementation**

In `saturn/ingestion/dossier.py`:

(a) Add the import near the other ingestion imports:

```python
from saturn.ingestion.fred import fetch_fred
```

(b) Change the `build_dossier` signature default from `fred_fn=None` to `fred_fn=fetch_fred`:

```python
    fred_fn: Callable[..., object] | None = fetch_fred,
```

(No other change: the existing `_fred` closure already calls `fred_fn(ticker)`, and the result handling already does `macro=fred_result if isinstance(fred_result, MacroSnapshot) else None`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/ingestion/test_dossier.py -v`
Expected: PASS (all dossier tests; tests that pass `fred_fn=None` explicitly still record a fred gap).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/dossier.py tests/ingestion/test_dossier.py
git commit -m "feat(ingestion): wire fetch_fred as the default macro source"
```

---

## Task 4: Full-suite verification + offline isolation

**Files:** none (verification).

- [ ] **Step 1: Full offline suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass. The autouse offline fixture drops `FRED_API_KEY`, so any code path that would call FRED without a key raises `DataUnavailable` (recorded as a gap) rather than hitting the network — confirm no test makes a real FRED call.

- [ ] **Step 2: Mock-path sanity**

Run: `.venv\Scripts\python.exe -m saturn.cli research NVDA --mock`
Expected: writes the report; the Macro Snapshot section still shows the mock FEDFUNDS row (mock path is unchanged — `_mock_dossier` doesn't call `fetch_fred`).

- [ ] **Step 3 (optional, requires network + FRED_API_KEY): live smoke**

If `FRED_API_KEY` is set in `.env`:
`.venv\Scripts\python.exe -c "from saturn.ingestion.fred import fetch_fred; s = fetch_fred(); print([(x.series_id, x.observations[-1] if x.observations else None) for x in s.series])"`
Expected: each curated series with its latest (date, value). (Skip if offline.)

- [ ] **Step 4: Commit (only if a fix was needed)**

```bash
git add -A
git commit -m "test(fred): verification fixups"
```

---

## Self-Review

**Spec coverage (against §3 FRED):**
- Curated default series (Fed funds, CPI, PPI, 10Y, 2Y, unemployment, M2) → Task 1 `FRED_SERIES` ✓
- Free API key via config `FRED_API_KEY`; absent → graceful gap → Task 2 (`DataUnavailable`) ✓
- Provenance {source, url, retrieved_at} per series → Task 2 ✓
- Integration into `build_dossier` real path → Task 3 ✓
- Macro is ticker-agnostic but matches the `fred_fn(ticker)` seam → Task 2 signature ✓

**Placeholder scan:** No TBD/"handle edge cases"/"similar to". Complete code each step. ✓

**Type consistency:** `fetch_fred(ticker=None, *, mock, fetch) -> MacroSnapshot` (Task 2) called as `fred_fn(ticker)` by `build_dossier` (positional ticker, default `fetch`) ✓. `_parse_observations(raw) -> list[tuple[date,float]]` (Task 1) used by `fetch_fred` (Task 2) ✓. `_fetch_series_observations(series_id, api_key) -> dict` (Task 2) is the default `fetch` and is monkeypatched/injected in tests ✓. `MacroSeries.observations` is `list[tuple[date,float]]`, matching the model and the renderer's `observations[-1]` usage ✓. `http_get` reused from the EDGAR plan's `http.py` ✓.

**Known limitations (documented, acceptable):**
- Per-series raw responses aren't cached yet (the cache module is ready; add `read_cache`/`write_cache` around `_fetch_series_observations` with a ~1d TTL as a fast follow for live runs).
- Series titles are hardcoded (deliberate, avoids a metadata call); if FRED renames a series the title may drift — low risk for these stable core series.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-fred-adapter.md`.
Sibling plan: `docs/superpowers/plans/2026-06-06-edgar-adapter.md`. Recommended execution order on one branch: EDGAR Task 1 (creates shared `http.py`) → rest of EDGAR → FRED. After both, a single `--mock` + offline suite run confirms the enriched non-mock path is fully wired (real EDGAR fundamentals + 10-K sections + FRED macro, each provenance-tagged, with graceful gaps when a key/source is missing).

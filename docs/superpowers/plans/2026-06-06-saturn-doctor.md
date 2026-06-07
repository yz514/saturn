# `saturn doctor` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `saturn doctor [TICKER]` CLI command that live-checks all four data dependencies (Anthropic, yfinance, SEC EDGAR, FRED) and prints a pass/fail readiness report with a scriptable exit code.

**Architecture:** A new `saturn/diagnostics.py` holds a `CheckResult` model, one isolated `check_*` function per dependency (each wraps a real adapter call in try/except and never raises), plus `run_checks` (orchestration) and `format_report` (rendering). `saturn/cli.py` gains a thin `doctor` command. Unit tests monkeypatch the adapters so the suite stays fully offline; the live calls themselves are exercised only when the user runs the command.

**Tech Stack:** Python 3.13, Typer (+ `typer.testing.CliRunner`), Pydantic v2, pytest. Reuses existing adapters: `fetch_quote` (prices), `fetch_edgar`, `fetch_fred`, `AnthropicClient`, and typed `IngestionError`.

**Spec:** `docs/superpowers/specs/2026-06-06-saturn-doctor-design.md`. Branch `saturn-doctor` off `main` (PR #6 merged, so `fetch_edgar` returns `material_events`).

**Platform note (important):** the report uses **ASCII markers** (`[OK]` / `[FAIL]`) and a plain hyphen, NOT the spec's illustrative `✓/✗/—` — those are non-ASCII and raise `UnicodeEncodeError` on a default cp1252 Windows console when printed. Markdown *files* can use UTF-8 (written with `encoding="utf-8"`), but console output must stay ASCII-safe.

---

## File Structure

**Create:**
- `saturn/diagnostics.py` — `CheckResult`, `check_anthropic`, `check_yfinance`, `check_edgar`, `check_fred`, `run_checks`, `format_report`. One responsibility: dependency health-checking + reporting.
- `tests/test_diagnostics.py` — unit tests for every check + run_checks + format_report (adapters monkeypatched).

**Modify:**
- `saturn/cli.py` — add the `doctor` command (import `run_checks`/`format_report` from `saturn.diagnostics`).
- `tests/test_cli.py` — add CliRunner tests for `doctor` (exit 0 all-pass, exit 1 any-fail), with `run_checks` monkeypatched.

**Established patterns:** lazy/real adapters called inside functions; each check isolates failures via try/except → `CheckResult`; typed `IngestionError` (`DataUnavailable`/`SourceFailure`) caught for the EDGAR/FRED gaps; offline tests via monkeypatch; venv interpreter `.venv\Scripts\python.exe`; no `__init__.py` under `tests/`.

---

## Task 1: `CheckResult` + `check_anthropic`

**Files:**
- Create: `saturn/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_diagnostics.py`:

```python
from types import SimpleNamespace

from saturn.diagnostics import CheckResult, check_anthropic


class _FakeClient:
    def __init__(self, api_key, default_model):
        self.default_model = default_model

    def complete(self, system, prompt, *, model=None):
        return "OK"


def test_check_anthropic_missing_key():
    r = check_anthropic(SimpleNamespace(anthropic_api_key=None))
    assert isinstance(r, CheckResult)
    assert r.name == "Anthropic"
    assert r.ok is False
    assert "ANTHROPIC_API_KEY not set" in r.detail


def test_check_anthropic_ping_ok(monkeypatch):
    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _FakeClient)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is True
    assert "claude-haiku-4-5" in r.detail


def test_check_anthropic_error_is_caught(monkeypatch):
    class _Boom:
        def __init__(self, *a):
            raise RuntimeError("bad key")

    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _Boom)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is False
    assert "bad key" in r.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'saturn.diagnostics'`.

- [ ] **Step 3: Write minimal implementation** — create `saturn/diagnostics.py`:

```python
"""Live dependency checks for `saturn doctor`.

Each check wraps one real adapter/credential and returns a CheckResult; a check
never raises. The live network calls here are intentional (this is the one
non-offline command). Unit tests monkeypatch the adapters to stay offline.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from saturn.llm.anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)

_PING_MODEL = "claude-haiku-4-5"  # cheapest model — this only proves the key works


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str


def check_anthropic(settings) -> CheckResult:
    """Verify the Anthropic key by a tiny live ping on the cheapest model."""
    if not settings.anthropic_api_key:
        return CheckResult(name="Anthropic", ok=False, detail="ANTHROPIC_API_KEY not set")
    try:
        client = AnthropicClient(settings.anthropic_api_key, _PING_MODEL)
        reply = client.complete("You are a health check.", "Reply with the single word: OK")
        if reply and reply.strip():
            return CheckResult(name="Anthropic", ok=True, detail=f"key works ({_PING_MODEL} responded)")
        return CheckResult(name="Anthropic", ok=False, detail="empty response from model")
    except Exception as exc:  # noqa: BLE001 - a check never raises
        return CheckResult(name="Anthropic", ok=False, detail=str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(diagnostics): add CheckResult and Anthropic key ping check"
```

---

## Task 2: `check_yfinance`, `check_edgar`, `check_fred`

**Files:**
- Modify: `saturn/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_diagnostics.py`:

```python
from datetime import date

from saturn.diagnostics import check_edgar, check_fred, check_yfinance
from saturn.ingestion.errors import DataUnavailable
from saturn.models import (
    FinancialFact,
    Fundamentals,
    MacroSeries,
    MacroSnapshot,
    Provenance,
    Quote,
)


def test_check_yfinance_ok(monkeypatch):
    monkeypatch.setattr(
        "saturn.diagnostics.fetch_quote",
        lambda ticker: Quote(price=228.5, market_cap=3_400_000_000_000.0, currency="USD", provenance=Provenance(source="yfinance")),
    )
    r = check_yfinance("AAPL")
    assert r.name == "yfinance" and r.ok is True
    assert "228" in r.detail


def test_check_yfinance_error(monkeypatch):
    def boom(ticker):
        raise RuntimeError("network down")

    monkeypatch.setattr("saturn.diagnostics.fetch_quote", boom)
    r = check_yfinance("AAPL")
    assert r.ok is False and "network down" in r.detail


def test_check_edgar_ok(monkeypatch):
    def fake_edgar(ticker):
        return {
            "fundamentals": Fundamentals(facts=[FinancialFact(concept="Revenues", value=1.0, provenance=Provenance(source="SEC EDGAR"))]),
            "filing_sections": [],
            "material_events": [],
            "name": "Apple Inc.",
            "cik": "0000320193",
        }

    monkeypatch.setattr("saturn.diagnostics.fetch_edgar", fake_edgar)
    r = check_edgar("AAPL")
    assert r.name == "SEC EDGAR" and r.ok is True
    assert "Apple Inc." in r.detail and "0000320193" in r.detail and "1 facts" in r.detail


def test_check_edgar_data_unavailable(monkeypatch):
    def boom(ticker):
        raise DataUnavailable("SEC_USER_AGENT not set; required for SEC EDGAR access")

    monkeypatch.setattr("saturn.diagnostics.fetch_edgar", boom)
    r = check_edgar("AAPL")
    assert r.ok is False and "SEC_USER_AGENT not set" in r.detail


def test_check_fred_ok(monkeypatch):
    def fake_fred():
        return MacroSnapshot(series=[
            MacroSeries(series_id="FEDFUNDS", title="Fed Funds", observations=[(date(2026, 4, 1), 4.33)], provenance=Provenance(source="FRED")),
        ])

    monkeypatch.setattr("saturn.diagnostics.fetch_fred", fake_fred)
    r = check_fred()
    assert r.name == "FRED" and r.ok is True
    assert "FEDFUNDS" in r.detail and "1 series" in r.detail


def test_check_fred_data_unavailable(monkeypatch):
    def boom():
        raise DataUnavailable("FRED_API_KEY not set")

    monkeypatch.setattr("saturn.diagnostics.fetch_fred", boom)
    r = check_fred()
    assert r.ok is False and "FRED_API_KEY not set" in r.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -k "yfinance or edgar or fred" -v`
Expected: FAIL with `ImportError: cannot import name 'check_yfinance'`.

- [ ] **Step 3: Write minimal implementation** — add to `saturn/diagnostics.py`. Extend the imports at the top:

```python
from saturn.ingestion.edgar import fetch_edgar
from saturn.ingestion.errors import IngestionError
from saturn.ingestion.fred import fetch_fred
from saturn.ingestion.prices import fetch_quote
```

Add a money formatter and the three checks:

```python
def _money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def check_yfinance(ticker: str) -> CheckResult:
    try:
        q = fetch_quote(ticker)
        if q.price is None:
            return CheckResult(name="yfinance", ok=False, detail="no price returned")
        return CheckResult(name="yfinance", ok=True, detail=f"price {_money(q.price)}, market cap {_money(q.market_cap)}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="yfinance", ok=False, detail=str(exc))


def check_edgar(ticker: str) -> CheckResult:
    try:
        r = fetch_edgar(ticker)
        fund = r.get("fundamentals")
        nfacts = len(fund.facts) if fund else 0
        nsec = len(r.get("filing_sections") or [])
        nev = len(r.get("material_events") or [])
        detail = f"{r.get('name')} (CIK {r.get('cik')}) - {nfacts} facts, {nsec} sections, {nev} events"
        return CheckResult(name="SEC EDGAR", ok=True, detail=detail)
    except IngestionError as exc:
        return CheckResult(name="SEC EDGAR", ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="SEC EDGAR", ok=False, detail=str(exc))


def check_fred() -> CheckResult:
    try:
        snap = fetch_fred()
        if not snap.series:
            return CheckResult(name="FRED", ok=False, detail="no series returned")
        first = snap.series[0]
        latest = first.observations[-1] if first.observations else None
        example = f", e.g. {first.series_id} {latest[1]} ({latest[0]})" if latest else ""
        return CheckResult(name="FRED", ok=True, detail=f"{len(snap.series)} series{example}")
    except IngestionError as exc:
        return CheckResult(name="FRED", ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="FRED", ok=False, detail=str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -v`
Expected: PASS (all diagnostics tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(diagnostics): add yfinance/EDGAR/FRED live checks"
```

---

## Task 3: `run_checks` + `format_report`

**Files:**
- Modify: `saturn/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_diagnostics.py`:

```python
from saturn.diagnostics import format_report, run_checks


def test_run_checks_returns_four(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("saturn.diagnostics.check_anthropic", lambda s: CheckResult(name="Anthropic", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_yfinance", lambda t: CheckResult(name="yfinance", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_edgar", lambda t: CheckResult(name="SEC EDGAR", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_fred", lambda: CheckResult(name="FRED", ok=True, detail="x"))
    results = run_checks("AAPL", settings=SimpleNamespace(anthropic_api_key="k"))
    assert [r.name for r in results] == ["Anthropic", "yfinance", "SEC EDGAR", "FRED"]


def test_format_report_marks_and_summary():
    results = [
        CheckResult(name="Anthropic", ok=True, detail="key works"),
        CheckResult(name="FRED", ok=False, detail="FRED_API_KEY not set"),
    ]
    out = format_report("AAPL", results)
    assert "Saturn doctor - ticker: AAPL" in out
    assert "[OK]" in out and "[FAIL]" in out
    assert "key works" in out and "FRED_API_KEY not set" in out
    assert "1/2 checks passed." in out
    # ASCII-safe: no non-ASCII chars (Windows console)
    out.encode("ascii")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -k "run_checks or format_report" -v`
Expected: FAIL with `ImportError: cannot import name 'run_checks'`.

- [ ] **Step 3: Write minimal implementation** — add to `saturn/diagnostics.py`:

```python
def run_checks(ticker: str, *, settings) -> list[CheckResult]:
    """Run all dependency checks (Anthropic, yfinance, EDGAR, FRED) in order."""
    return [
        check_anthropic(settings),
        check_yfinance(ticker),
        check_edgar(ticker),
        check_fred(),
    ]


def format_report(ticker: str, results: list[CheckResult]) -> str:
    """Render an ASCII-safe readiness report (Windows-console friendly)."""
    lines = [f"Saturn doctor - ticker: {ticker}", ""]
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        mark = "[OK]  " if r.ok else "[FAIL]"
        lines.append(f"{mark} {r.name.ljust(width)}  {r.detail}")
    passed = sum(1 for r in results if r.ok)
    lines.append("")
    lines.append(f"{passed}/{len(results)} checks passed.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(diagnostics): add run_checks orchestration and ASCII-safe report"
```

---

## Task 4: `doctor` CLI command

**Files:**
- Modify: `saturn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_cli.py` (it already imports the Typer `app`; if it doesn't, add `from saturn.cli import app` and `from typer.testing import CliRunner`):

```python
from typer.testing import CliRunner

from saturn.diagnostics import CheckResult


def test_doctor_all_pass_exit_zero(monkeypatch):
    monkeypatch.setattr(
        "saturn.cli.run_checks",
        lambda ticker, *, settings: [
            CheckResult(name="Anthropic", ok=True, detail="key works"),
            CheckResult(name="yfinance", ok=True, detail="price $1"),
        ],
    )
    result = CliRunner().invoke(app, ["doctor", "AAPL"])
    assert result.exit_code == 0
    assert "2/2 checks passed." in result.stdout


def test_doctor_any_fail_exit_one(monkeypatch):
    monkeypatch.setattr(
        "saturn.cli.run_checks",
        lambda ticker, *, settings: [
            CheckResult(name="Anthropic", ok=True, detail="key works"),
            CheckResult(name="FRED", ok=False, detail="FRED_API_KEY not set"),
        ],
    )
    result = CliRunner().invoke(app, ["doctor"])  # default ticker
    assert result.exit_code == 1
    assert "[FAIL]" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -k "doctor" -v`
Expected: FAIL — no `doctor` command registered (Typer exits with usage error / non-zero, or `run_checks` import missing).

- [ ] **Step 3: Write minimal implementation** — in `saturn/cli.py`:

Add the import (near the other `from saturn...` imports):
```python
from saturn.diagnostics import format_report, run_checks
```

Add the command (after the `research` command, before `def main()`):
```python
@app.command()
def doctor(
    ticker: str = typer.Argument("AAPL", help="Ticker to live-check, e.g. AAPL"),
) -> None:
    """Live-check Saturn's data dependencies (Anthropic, yfinance, EDGAR, FRED)."""
    settings = get_settings()
    setup_logging(settings.log_level)
    ticker = ticker.upper()
    results = run_checks(ticker, settings=settings)
    typer.echo(format_report(ticker, results))
    if any(not r.ok for r in results):
        raise typer.Exit(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS (the new doctor tests + existing CLI tests). Then `.venv\Scripts\python.exe -m pytest -q` (full suite).

- [ ] **Step 5: Commit**

```bash
git add saturn/cli.py tests/test_cli.py
git commit -m "feat(cli): add `saturn doctor` live dependency-check command"
```

---

## Task 5: Full-suite + offline verification

**Files:** none (verification).

- [ ] **Step 1: Full offline suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (prior suite + ~11 new diagnostics/cli tests). Fix any regression.

- [ ] **Step 2: Confirm offline isolation**

Confirm no diagnostics/cli test performs real network or a real Anthropic call: every test monkeypatches `fetch_quote`/`fetch_edgar`/`fetch_fred`/`AnthropicClient` or `run_checks`. Run `.venv\Scripts\python.exe -m pytest tests/test_diagnostics.py tests/test_cli.py -v` and confirm green with no network.

- [ ] **Step 3: Help-text smoke (offline, no network)**

Run: `.venv\Scripts\python.exe -m saturn.cli doctor --help`
Expected: shows the `doctor` usage + the TICKER argument help; exits 0. (This does not run the checks.)

- [ ] **Step 4 (optional, requires creds + network): live run**

If `.env` has `ANTHROPIC_API_KEY`, `SEC_USER_AGENT`, and `FRED_API_KEY`, run:
`.venv\Scripts\python.exe -m saturn.cli doctor AAPL`
Expected: four `[OK]` lines (or `[FAIL]` for any unset/blocked dep), a `N/4 checks passed.` summary, exit 0 if all pass. This is the real-world validation step. (Skip if offline.)

- [ ] **Step 5: Commit any fix-ups**

```bash
git add -A
git commit -m "test: verify saturn doctor end-to-end (offline)"
```

---

## Self-Review

**Spec coverage (against `2026-06-06-saturn-doctor-design.md`):**
- `CheckResult` + four isolated checks → Tasks 1–2 ✓
- Anthropic cheapest-model ping, missing-key path → Task 1 ✓
- yfinance/EDGAR/FRED live checks with `DataUnavailable`/`SourceFailure` handling → Task 2 ✓ (caught via `IngestionError` base + broad except)
- `run_checks` + `format_report` → Task 3 ✓
- `doctor` command, exit 0 all-pass / 1 any-fail, default ticker AAPL → Task 4 ✓
- offline test integrity (monkeypatched adapters, no real call) → all tasks; Task 5 verifies ✓
- ASCII-safe report (Windows console) → Task 3 (`format_report` + `out.encode("ascii")` assertion) ✓ [deliberate deviation from the spec's illustrative ✓/✗]

**Placeholder scan:** No TBD/"handle edge cases"/"similar to". Every code step complete. ✓

**Type consistency:** `CheckResult{name,ok,detail}` (Task 1) used identically in Tasks 2–4. `check_anthropic(settings)`, `check_yfinance(ticker)`, `check_edgar(ticker)`, `check_fred()` (Tasks 1–2) called by `run_checks(ticker, *, settings)` (Task 3) and monkeypatched in tests. `run_checks`/`format_report` imported into `cli.py` (Task 4). `_PING_MODEL = "claude-haiku-4-5"`, asserted in Task 1's test. EDGAR detail counts read `fundamentals.facts`, `filing_sections`, `material_events` from the `fetch_edgar` dict contract. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-saturn-doctor.md`. Recommended execution: subagent-driven (fresh subagent per task, two-stage review), same as prior slices. Small plan (5 tasks).

# Gap Honesty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EDGAR records a diagnostic gap when it yields no usable facts, and the CLI refuses to research a company whose fundamentals are absent.

**Architecture:** `_parse_companyfacts` (which holds both the raw blob and the assembled facts) raises `DataUnavailable` naming the row count and SEC forms it saw; `route_to_source` already converts that into a recorded `SourceGap`, exactly as the `industry` source does today. The CLI then gates on `fundamentals.facts` before any LLM call.

**Tech Stack:** Python 3.13, Pydantic v2, typer, pytest. Runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-gap-honesty-design.md`

**File structure:**
- `saturn/ingestion/edgar.py` — `_survey_forms` helper + the raise in `_parse_companyfacts` (T1)
- `saturn/cli.py` — the no-fundamentals gate (T2)

**Context — the bug.** A live ASML dossier had zero fundamentals/filings/events and `GAPS: []`.
`route_to_source` only creates a gap in its `except` clauses; EDGAR never raises — it parses fine, then
the form filters (`edgar.py:97` `if not form.startswith("10-K")`, `edgar.py:108` `... not
form.startswith("10-Q")`) drop every row, because ASML is a foreign private issuer filing only 20-F.
Zero facts is indistinguishable from success.

---

### Task 1: EDGAR raises a diagnostic `DataUnavailable`

**Files:**
- Modify: `saturn/ingestion/edgar.py` (import; new `_survey_forms` helper; the raise inside `_parse_companyfacts`, which currently ends `return Fundamentals(facts=facts)` at ~line 203)
- Test: `tests/ingestion/test_edgar.py`, `tests/ingestion/test_dossier.py`

- [ ] **Step 1: Write the failing tests**

FIRST read `tests/ingestion/test_edgar.py` and find any existing fixture/helper that builds a valid
companyfacts blob for `_parse_companyfacts`. **Reuse it for the regression guard below** rather than
inventing one — `_parse_companyfacts` requires specific row fields (`form`, `fp`, `start`/`end`,
`val`, `filed`, `accn`) and a concept present in `EDGAR_CONCEPTS`. If no such fixture exists, build a
minimal one and iterate until the guard passes; do NOT weaken the assertion to make it pass.

Add to `tests/ingestion/test_edgar.py`:

```python
import pytest
from saturn.ingestion.edgar import _parse_companyfacts, _survey_forms
from saturn.ingestion.errors import DataUnavailable


def test_survey_forms_counts_rows_and_distinct_forms():
    blob = {"facts": {"us-gaap": {
        "Revenues": {"units": {"EUR": [{"form": "20-F"}, {"form": "20-F"}]}},
        "NetIncomeLoss": {"units": {"EUR": [{"form": "20-F/A"}, {"form": "20-F"}]}}}}}
    n, forms = _survey_forms(blob)
    assert n == 4 and forms == ["20-F", "20-F/A"]


def test_survey_forms_empty_without_us_gaap():
    assert _survey_forms({"facts": {"dei": {}}}) == (0, [])
    assert _survey_forms({}) == (0, [])


def test_parse_companyfacts_raises_for_a_foreign_private_issuer():
    # ASML-like: every row is a 20-F, so the 10-K/10-Q filters drop everything.
    blob = {"facts": {"us-gaap": {"Revenues": {"units": {"EUR": [
        {"form": "20-F", "fp": "FY", "start": "2025-01-01", "end": "2025-12-31",
         "val": 32667300000, "filed": "2026-02-11", "accn": "x"}]}}}}}
    with pytest.raises(DataUnavailable) as exc:
        _parse_companyfacts(blob)
    msg = str(exc.value)
    assert "20-F" in msg and "10-K/10-Q" in msg      # the reason must explain WHY


def test_parse_companyfacts_raises_without_any_xbrl_facts():
    with pytest.raises(DataUnavailable, match="no XBRL facts"):
        _parse_companyfacts({"facts": {}})
```

Add to `tests/ingestion/test_dossier.py` (mirror how the existing tests inject stub source fns — read
`build_dossier`'s signature and the neighbouring tests first):

```python
def test_build_dossier_records_an_edgar_gap_instead_of_going_silent():
    # The ASML case: EDGAR raises -> route_to_source must turn it into a RECORDED gap, not a crash
    # and not silence. Guards the SourceGap contract: "a source that could not contribute, recorded".
    from saturn.ingestion.errors import DataUnavailable

    def _edgar_boom(ticker):
        raise DataUnavailable("0 usable facts from 24 XBRL rows (forms seen: 20-F); "
                              "Saturn reads 10-K/10-Q only")

    d = build_dossier("ASML", mock=False, edgar_fn=_edgar_boom)   # add the other stub fns as the
                                                                  # neighbouring tests do
    assert d.fundamentals.facts == []
    assert any(g.source == "edgar" and "20-F" in g.reason for g in d.gaps)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py -k "survey_forms or parse_companyfacts_raises" tests/ingestion/test_dossier.py -k "records_an_edgar_gap" -v`
Expected: FAIL — `ImportError: cannot import name '_survey_forms'`; `_parse_companyfacts` returns an empty `Fundamentals` instead of raising.

- [ ] **Step 3: Implement**

(3a) In `saturn/ingestion/edgar.py`, add the errors import alongside the existing imports:
```python
from saturn.ingestion.errors import DataUnavailable
```
(Check first — if `edgar.py` already imports from `saturn.ingestion.errors`, extend that line instead.)

(3b) Add this pure helper immediately ABOVE `def _parse_companyfacts(`:
```python
def _survey_forms(blob: dict) -> tuple[int, list[str]]:
    """Count us-gaap XBRL rows and the distinct SEC forms they came from — used to explain WHY an
    extraction produced nothing. Pure; (0, []) when the blob carries no us-gaap facts."""
    forms: set[str] = set()
    n = 0
    for tag_block in ((blob.get("facts") or {}).get("us-gaap") or {}).values():
        for rows in (tag_block.get("units") or {}).values():
            for row in rows:
                n += 1
                form = row.get("form")
                if form:
                    forms.add(str(form))
    return n, sorted(forms)
```

(3c) In `_parse_companyfacts`, replace the final `return Fundamentals(facts=facts)` with:
```python
    if not facts:
        # Zero facts is NOT success. Say what was actually there so the recorded gap explains itself:
        # a foreign private issuer (20-F/6-K) has plenty of us-gaap rows, all dropped by the
        # 10-K/10-Q form filters above.
        n_rows, forms = _survey_forms(raw)
        if n_rows == 0:
            raise DataUnavailable("no XBRL facts published for this company")
        raise DataUnavailable(
            f"0 usable facts from {n_rows:,} XBRL rows (forms seen: {', '.join(forms)}); "
            f"Saturn reads 10-K/10-Q only")
    return Fundamentals(facts=facts)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py tests/ingestion/test_dossier.py -q` → green.
Then: `.venv/Scripts/python.exe -m pytest -q` → **FULL suite green**.

⚠️ **This change sits in the path every US ticker depends on.** If any existing test now fails because a
fixture produced zero facts and previously returned an empty `Fundamentals` silently, that is a REAL
finding — report it; do NOT weaken the new raise. Confirm the raise propagates out through
`fetch_edgar` (the `test_build_dossier_records_an_edgar_gap_instead_of_going_silent` test proves it: if
`fetch_edgar` swallowed it, no gap would be recorded).

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py tests/ingestion/test_dossier.py
git commit -m "fix(edgar): raise a diagnostic DataUnavailable when no usable facts survive"
```

---

### Task 2: the CLI refuses to research without fundamentals

**Files:**
- Modify: `saturn/cli.py` (insert between the `build_dossier` try/except and the `run` try/except, ~line 60)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

FIRST read `tests/test_cli.py` and mirror its existing invocation pattern (runner construction, app
object, and how it passes `--mock`). Then add:

```python
def test_cli_refuses_without_fundamentals_and_never_calls_run(monkeypatch):
    # The ASML case: no fundamentals => the Critic can ground nothing, so spend NO LLM calls.
    from datetime import date
    import saturn.cli as cli
    from saturn.models import CompanyDossier, Fundamentals, SourceGap

    d = CompanyDossier(
        ticker="ASML", name="ASML Holding N.V.", generated_at=date.today(),
        fundamentals=Fundamentals(facts=[]),
        gaps=[SourceGap(source="edgar",
                        reason="0 usable facts from 24 XBRL rows (forms seen: 20-F); "
                               "Saturn reads 10-K/10-Q only")])
    monkeypatch.setattr(cli, "build_dossier", lambda ticker, mock=False: d)

    def _never_run(*args, **kwargs):
        raise AssertionError("run() must not be called when fundamentals are absent")
    monkeypatch.setattr(cli, "run", _never_run)

    result = <runner>.invoke(cli.app, ["research", "ASML", "--mock"])
    assert result.exit_code == 1
    assert "insufficient data" in result.output
    assert "20-F" in result.output                      # the gap reason is surfaced to the user
    assert "No report written" in result.output
```
Replace `<runner>` with whatever the existing tests use (e.g. a module-level `CliRunner()`). Use
`--mock` so no API key is needed. If the runner separates stderr, read `result.stderr` instead of
`result.output` — check how the neighbouring CLI tests assert on error output.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -k refuses_without_fundamentals -v`
Expected: FAIL — `AssertionError: run() must not be called when fundamentals are absent` (the gate does not exist, so the CLI proceeds to `run`).

- [ ] **Step 3: Implement**

In `saturn/cli.py`, the seam currently reads:
```python
    try:
        company = build_dossier(ticker, mock=mock)
    except IngestionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    try:
        report = run(company, llm, model_used=model_used, mock=mock)
```
Insert the gate between the two blocks:
```python
    # Without as-reported facts the Critic can ground nothing, so a report would be unguarded — not
    # merely thin. Refuse before spending any LLM call, and say which source came back empty.
    if not company.fundamentals.facts:
        typer.echo(f"{ticker}: insufficient data to research.", err=True)
        for g in company.gaps:
            typer.echo(f"  {g.source}: {g.reason}", err=True)
        typer.echo("No report written.", err=True)
        raise typer.Exit(1)
```

Do NOT put this gate in `run()`: `run()` is called directly by the test suite and offline scripts with
hand-built dossiers, and gating there would be a wide blast radius for no benefit. The CLI is the entry
point that spends money.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -q` → green (the existing CLI tests use a
mock dossier, which HAS facts, so the gate must not fire on them).
Then: `.venv/Scripts/python.exe -m pytest -q` → **FULL suite green**.

- [ ] **Step 5: Commit**

```bash
git add saturn/cli.py tests/test_cli.py
git commit -m "feat(cli): refuse to research a company with no fundamentals (0 LLM calls)"
```

---

## Final verification (after both tasks)

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest -q`.
- [ ] **The ASML case, live and free** (network only, no LLM):
  ```
  .venv/Scripts/python.exe -c "
  from saturn.ingestion.dossier import build_dossier
  d = build_dossier('ASML', mock=False)
  print('facts:', len(d.fundamentals.facts))
  print('GAPS:', [(g.source, g.reason) for g in d.gaps])"
  ```
  Expect `facts: 0` and a **recorded** `edgar` gap naming `20-F` — where today it prints `GAPS: []`.
- [ ] **The refusal, end to end:** `.venv/Scripts/python.exe -m saturn.cli research ASML` prints the
  refusal, writes no report, exits 1, and makes zero LLM calls.
- [ ] **No regression for a US ticker:** `.venv/Scripts/python.exe -c "
  from saturn.ingestion.dossier import build_dossier
  d = build_dossier('MSFT', mock=False); print('MSFT facts:', len(d.fundamentals.facts))"`
  → a healthy non-zero count (MSFT must be entirely unaffected).

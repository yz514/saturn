# LLM Truncation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `saturn research <real ticker>` from crashing on truncated analyze/debate JSON by giving the model more output room, bounding the LLM-facing context, and failing gracefully on bad responses.

**Architecture:** Thread `max_tokens` through the `LLMClient` Protocol and both clients (analyze/debate request 4096). Bound what `_company_context` renders into the prompt (recent-only facts + trimmed section/event excerpts — dossier/report/cache keep full data). Add a typed `LLMResponseError` raised on parse failure and caught cleanly by the CLI.

**Tech Stack:** Python 3.13, Pydantic v2 (`ValidationError`), Typer (+ CliRunner), pytest. Touches the LLM layer, the workflow, and the CLI.

**Spec:** `docs/superpowers/specs/2026-06-06-llm-truncation-fix-design.md`. Branch `fix-llm-truncation` off `main`.

---

## File Structure

**Modify:**
- `saturn/llm/base.py` — add `max_tokens` to the `LLMClient.complete` Protocol.
- `saturn/llm/anthropic_client.py` — `complete` accepts + forwards `max_tokens`.
- `saturn/llm/mock_client.py` — `complete` accepts (ignores) `max_tokens`.
- `saturn/workflows/equity_research.py` — output-token constant; `LLMResponseError` + graceful parse; context-bounding constants + trimming in `_company_context`; analyze/debate pass `max_tokens`.
- `saturn/cli.py` — catch `LLMResponseError` in `research`.

**Tests:** extend the existing LLM/workflow/CLI test files (locate with grep as noted per task).

**Established patterns:** provider-agnostic `LLMClient` Protocol; pure `_build_params`; typed errors caught at the CLI boundary; offline tests (no real LLM/network); venv `.venv\Scripts\python.exe`; no `__init__.py` under `tests/`.

---

## Task 1: Thread `max_tokens` through the LLM interface

**Files:**
- Modify: `saturn/llm/base.py`, `saturn/llm/anthropic_client.py`, `saturn/llm/mock_client.py`
- Test: the existing test for `_build_params` (find with `grep -rl "_build_params" tests`) and for `MockLLMClient` (find with `grep -rl "MockLLMClient" tests`).

- [ ] **Step 1: Write the failing test**

Find the test file that imports `_build_params` (run `grep -rl "_build_params" tests`) and add:

```python
def test_build_params_carries_max_tokens():
    from saturn.llm.anthropic_client import _build_params

    params = _build_params("sys", "prompt", "claude-sonnet-4-6", max_tokens=4096)
    assert params["max_tokens"] == 4096
```

Find the test file that imports `MockLLMClient` (run `grep -rl "MockLLMClient" tests`) and add:

```python
def test_mock_client_accepts_max_tokens():
    from saturn.llm.mock_client import MockLLMClient

    out = MockLLMClient().complete("sys", "OUTPUT_SCHEMA=analysis", max_tokens=4096)
    assert "executive_summary" in out
```

(If no such test files exist, create `tests/test_llm.py` with both tests plus the needed imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest -k "max_tokens" -v`
Expected: `test_mock_client_accepts_max_tokens` FAILs with `TypeError: complete() got an unexpected keyword argument 'max_tokens'` (the `_build_params` test already passes since the param exists — that's fine; it locks the contract).

- [ ] **Step 3: Write minimal implementation**

In `saturn/llm/base.py`, update the Protocol method signature:

```python
    def complete(
        self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000
    ) -> str:
        """Return the model's text response to `prompt` under `system`."""
        ...
```

In `saturn/llm/anthropic_client.py`, update `complete` to accept and forward `max_tokens`:

```python
    def complete(
        self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000
    ) -> str:
        params = _build_params(system, prompt, model or self._default_model, max_tokens=max_tokens)
        response = self._client.messages.create(**params)
        return "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
```

In `saturn/llm/mock_client.py`, update `complete` to accept (and ignore) `max_tokens`:

```python
    def complete(
        self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000
    ) -> str:
        if "OUTPUT_SCHEMA=debate" in prompt:
            return _DEBATE
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return _ANALYSIS
        return "{}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest -k "max_tokens" -v`
Expected: PASS (both). Then `.venv\Scripts\python.exe -m pytest -q` (full suite — the existing `_build_params` and mock tests still pass).

- [ ] **Step 5: Commit**

```bash
git add saturn/llm/base.py saturn/llm/anthropic_client.py saturn/llm/mock_client.py tests/
git commit -m "feat(llm): thread max_tokens through the LLMClient interface"
```

---

## Task 2: analyze/debate request more tokens + parse gracefully

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_equity_research.py`:

```python
import pytest

from saturn.ingestion.dossier import _mock_dossier
from saturn.workflows.equity_research import (
    LLMResponseError,
    _MAX_OUTPUT_TOKENS,
    analyze,
    debate,
)


class _TruncatedClient:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return '{"executive_summary": "abc'  # truncated JSON


class _CapturingClient:
    def __init__(self):
        self.calls = []

    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        self.calls.append(max_tokens)
        if "OUTPUT_SCHEMA=debate" in prompt:
            return '{"bull_thesis": "b", "bear_thesis": "x", "final_view": "f"}'
        return (
            '{"executive_summary": "e", "company_overview": "c", '
            '"business_segments": "s", "financial_snapshot": "fs", '
            '"valuation_discussion": "v", "key_risks": "k", "open_questions": "o"}'
        )


def test_analyze_raises_llmresponseerror_on_truncated_json():
    with pytest.raises(LLMResponseError):
        analyze(_mock_dossier("NVDA"), _TruncatedClient())


def test_debate_raises_llmresponseerror_on_truncated_json():
    with pytest.raises(LLMResponseError):
        debate(_mock_dossier("NVDA"), _TruncatedClient())


def test_analyze_requests_max_output_tokens():
    client = _CapturingClient()
    analyze(_mock_dossier("NVDA"), client)
    assert client.calls == [_MAX_OUTPUT_TOKENS]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -k "llmresponseerror or max_output_tokens" -v`
Expected: FAIL with `ImportError: cannot import name 'LLMResponseError'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/workflows/equity_research.py`:

Add the import for pydantic's error near the top (after `from datetime import date`):

```python
from pydantic import ValidationError
```

Add a constant after `DEBATE_SYSTEM` (and the error class + parse helper after `_extract_json`):

```python
_MAX_OUTPUT_TOKENS = 4096
```

After the `_extract_json` function, add:

```python
class LLMResponseError(RuntimeError):
    """Raised when the LLM response can't be parsed into the expected schema."""


def _parse(model_cls, raw: str, schema: str):
    """Parse an LLM JSON response into `model_cls`, or raise LLMResponseError."""
    try:
        return model_cls.model_validate_json(_extract_json(raw))
    except (ValueError, ValidationError) as exc:
        raise LLMResponseError(
            f"model returned malformed or truncated JSON for {schema}"
        ) from exc
```

Replace the body of `analyze` so it requests the token cap and parses via `_parse`:

```python
def analyze(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> AnalysisSections:
    prompt = (
        "OUTPUT_SCHEMA=analysis\n"
        f"Company data:\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "executive_summary, company_overview, business_segments, "
        "financial_snapshot, valuation_discussion, key_risks, open_questions."
    )
    logger.info("analyze: %s", company.ticker)
    raw = llm.complete(ANALYSIS_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
    return _parse(AnalysisSections, raw, "analysis")
```

Replace the body of `debate` similarly:

```python
def debate(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> DebateSections:
    prompt = (
        "OUTPUT_SCHEMA=debate\n"
        f"Company data:\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "bull_thesis, bear_thesis, final_view."
    )
    logger.info("debate: %s", company.ticker)
    raw = llm.complete(DEBATE_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
    return _parse(DebateSections, raw, "debate")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS — the new tests plus the existing `test_run_accepts_dossier_and_builds_report` (the `MockLLMClient` returns valid JSON, parses fine, and now also receives `max_tokens` which it ignores). Then `.venv\Scripts\python.exe -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): request 4096 output tokens; raise LLMResponseError on bad JSON"
```

---

## Task 3: Bound the LLM-facing context

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_equity_research.py`:

```python
from datetime import date

from saturn.models import (
    CompanyDossier,
    FilingSection,
    FinancialFact,
    Fundamentals,
    MaterialEvent,
    Provenance,
)
from saturn.workflows.equity_research import (
    _CTX_MAX_ANNUAL,
    _CTX_MAX_EVENTS,
    _CTX_SECTION_CHARS,
    _company_context,
)


def _big_dossier() -> CompanyDossier:
    prov = Provenance(source="SEC EDGAR")
    facts = []
    for fy in range(2019, 2026):  # 7 annual years of Revenues
        facts.append(FinancialFact(concept="Revenues", value=float(fy), unit="USD", fiscal_period=f"FY{fy}", provenance=prov))
    for q in range(1, 7):  # 6 quarters
        facts.append(FinancialFact(concept="Revenues", value=float(q), unit="USD", fiscal_period=f"Q{((q-1)%4)+1} FY{2024 + (q//5)}", provenance=prov))
    events = [
        MaterialEvent(filing_date=date(2025, m, 1), item_codes=["2.02"], title=f"Event {m}", excerpt="E" * 2000, provenance=prov)
        for m in range(1, 11)  # 10 events
    ]
    return CompanyDossier(
        ticker="NVDA",
        name="NVIDIA",
        fundamentals=Fundamentals(facts=facts),
        filing_sections=[FilingSection(name="Risk Factors", excerpt="R" * 5000, provenance=prov)],
        material_events=events,
        generated_at=date(2026, 6, 6),
    )


def test_context_caps_annual_facts():
    ctx = _company_context(_big_dossier())
    # only the most-recent N annual years appear
    assert "FY2025" in ctx and "FY2024" in ctx and "FY2023" in ctx
    assert "FY2019" not in ctx and "FY2020" not in ctx
    assert _CTX_MAX_ANNUAL == 3


def test_context_trims_section_excerpt():
    ctx = _company_context(_big_dossier())
    # the 5000-char "RRRR..." excerpt is trimmed to the section char cap
    run_of_r = max((len(s) for s in ctx.split() if set(s) == {"R"}), default=0)
    assert run_of_r <= _CTX_SECTION_CHARS


def test_context_caps_events():
    ctx = _company_context(_big_dossier())
    assert ctx.count("MATERIAL EVENTS") == 1
    event_lines = [ln for ln in ctx.splitlines() if ln.startswith("- ") and "Event " in ln]
    assert len(event_lines) <= _CTX_MAX_EVENTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -k "context_caps or context_trims" -v`
Expected: FAIL with `ImportError: cannot import name '_CTX_MAX_ANNUAL'`.

- [ ] **Step 3: Write minimal implementation**

In `saturn/workflows/equity_research.py`, add constants near `_MAX_OUTPUT_TOKENS`:

```python
_CTX_MAX_ANNUAL = 3
_CTX_MAX_QUARTERS = 4
_CTX_SECTION_CHARS = 1200
_CTX_MAX_EVENTS = 6
_CTX_EVENT_CHARS = 500
```

Add these period-parsing + fact-selection helpers (place them above `_company_context`):

```python
def _fy_num(period: str) -> int:
    """'FY2024' -> 2024; unparseable -> -1."""
    try:
        return int((period or "").replace("FY", "").strip())
    except (ValueError, AttributeError):
        return -1


def _q_sort(period: str) -> tuple[int, int]:
    """'Q2 FY2025' -> (2025, 2); unparseable -> (-1, -1)."""
    try:
        q_part, fy_part = period.split()
        return (int(fy_part.replace("FY", "")), int(q_part[1]))
    except (ValueError, AttributeError, IndexError):
        return (-1, -1)


def _select_context_facts(facts: list) -> list:
    """Per concept, keep the most-recent _CTX_MAX_ANNUAL annual + _CTX_MAX_QUARTERS
    quarterly facts (prompt budget control; the dossier keeps the full set)."""
    by_concept: dict[str, list] = {}
    for f in facts:
        by_concept.setdefault(f.concept, []).append(f)
    out: list = []
    for items in by_concept.values():
        annual = [x for x in items if (x.fiscal_period or "").startswith("FY")]
        quarterly = [x for x in items if (x.fiscal_period or "").startswith("Q")]
        annual.sort(key=lambda x: _fy_num(x.fiscal_period), reverse=True)
        quarterly.sort(key=lambda x: _q_sort(x.fiscal_period), reverse=True)
        out.extend(annual[:_CTX_MAX_ANNUAL])
        out.extend(quarterly[:_CTX_MAX_QUARTERS])
    return out
```

In `_company_context`, change the FUNDAMENTALS loop to iterate the bounded selection:

```python
    if dossier.fundamentals and dossier.fundamentals.facts:
        lines.append("\nFUNDAMENTALS (as-reported):")
        for fact in _select_context_facts(dossier.fundamentals.facts):
            cite = fact.provenance.source
            if fact.provenance.as_of:
                cite += f", as of {fact.provenance.as_of}"
            period = fact.fiscal_period or "?"
            lines.append(
                f"- {fact.concept} {period}: {fact.value} {fact.unit or ''} (source: {cite})"
            )
```

Change the FILING SECTIONS loop to trim the excerpt:

```python
    if dossier.filing_sections:
        lines.append("\nFILING SECTIONS:")
        for s in dossier.filing_sections:
            excerpt = (s.excerpt or "")[:_CTX_SECTION_CHARS]
            lines.append(f"- {s.name} (source: {s.provenance.source}): {excerpt}")
```

Change the MATERIAL EVENTS block to cap count + trim excerpt:

```python
    if dossier.material_events:
        lines.append("\nMATERIAL EVENTS (SEC 8-K):")
        recent = sorted(dossier.material_events, key=lambda e: e.filing_date, reverse=True)
        for ev in recent[:_CTX_MAX_EVENTS]:
            label = ev.title or ", ".join(ev.item_codes) or "8-K"
            lines.append(f"- {ev.filing_date}: {label} (source: {ev.provenance.source})")
            if ev.excerpt:
                lines.append(f"  {ev.excerpt[:_CTX_EVENT_CHARS]}")
```

(Leave QUOTE / MACRO / NEWS / DATA GAPS blocks unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_equity_research.py -v`
Expected: PASS — new bounding tests + existing context/run tests (the mock dossier has few facts/events, well under the caps, so its assertions still hold). Then `.venv\Scripts\python.exe -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research.py
git commit -m "feat(workflow): bound LLM-facing context (recent facts + trimmed excerpts)"
```

---

## Task 4: CLI catches `LLMResponseError`

**Files:**
- Modify: `saturn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_cli.py`:

```python
def test_research_handles_llm_response_error(monkeypatch):
    from typer.testing import CliRunner

    from saturn.cli import app
    from saturn.workflows.equity_research import LLMResponseError

    # Avoid needing a key / network: pretend a key is set, stub ingestion + run.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "testkey")
    monkeypatch.setattr("saturn.cli.build_dossier", lambda ticker, *, mock: object())

    def boom(*a, **k):
        raise LLMResponseError("model returned malformed or truncated JSON for analysis")

    monkeypatch.setattr("saturn.cli.run", boom)
    # AnthropicClient is constructed before run() is called; stub it so no real client.
    monkeypatch.setattr("saturn.cli.AnthropicClient", lambda *a, **k: object())

    result = CliRunner().invoke(app, ["research", "NVDA"])
    assert result.exit_code == 1
    assert "malformed or truncated JSON" in result.output
    # clean message, not a raw traceback
    assert "Traceback" not in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -k "llm_response_error" -v`
Expected: FAIL — `research` doesn't catch `LLMResponseError`, so the CliRunner result has `exit_code == 1` but `result.exception` is the `LLMResponseError` (uncaught) and the assertion on a clean message / no traceback fails (CliRunner surfaces the exception).

- [ ] **Step 3: Write minimal implementation**

In `saturn/cli.py`:

Add the import (next to `from saturn.ingestion.errors import IngestionError`):

```python
from saturn.workflows.equity_research import LLMResponseError, run
```

(Note: `run` is already imported from `saturn.workflows.equity_research`; combine the import so it reads `from saturn.workflows.equity_research import LLMResponseError, run` — do not duplicate the `run` import.)

Wrap the `run(...)` call in `research` with a try/except. Replace:

```python
    report = run(company, llm, model_used=model_used, mock=mock)
    markdown = render(report)
```

with:

```python
    try:
        report = run(company, llm, model_used=model_used, mock=mock)
    except LLMResponseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    markdown = render(report)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS (the new test + existing CLI tests). Then `.venv\Scripts\python.exe -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add saturn/cli.py tests/test_cli.py
git commit -m "feat(cli): exit cleanly on LLMResponseError instead of crashing"
```

---

## Task 5: Full-suite + offline verification

**Files:** none (verification).

- [ ] **Step 1: Full offline suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all pass. Fix any regression (e.g. a test that constructed a fake LLM client whose `complete` lacks the new `max_tokens` kwarg — add `max_tokens=2000` to its signature).

- [ ] **Step 2: Mock smoke run**

Run: `.venv\Scripts\python.exe -m saturn.cli research NVDA --mock`
Expected: `[MOCK MODE] Wrote reports\NVDA_<DATE>.md`, exit 0 (mock path unaffected — small context, valid JSON).

- [ ] **Step 3: Context-size spot check (offline)**

Run:
`.venv\Scripts\python.exe -c "from saturn.ingestion.dossier import _mock_dossier; from saturn.workflows.equity_research import _company_context; ctx = _company_context(_mock_dossier('NVDA')); print('context chars:', len(ctx))"`
Expected: prints a modest character count (the mock dossier is small); no error. (This just confirms the bounded renderer runs.)

- [ ] **Step 4 (optional, requires key + network): live run**

If `.env` has `ANTHROPIC_API_KEY` + `SEC_USER_AGENT`, run:
`.venv\Scripts\python.exe -m saturn.cli research AAPL`
Expected: completes and prints `Wrote reports\AAPL_<DATE>.md` (no truncation crash). Open the report and confirm the sections rendered. (Skip if offline — this is the real-world confirmation the bug is fixed.)

- [ ] **Step 5: Commit any fix-ups**

```bash
git add -A
git commit -m "test: verify LLM truncation fix end-to-end (offline)"
```

---

## Self-Review

**Spec coverage (against `2026-06-06-llm-truncation-fix-design.md`):**
- §1 max_tokens through Protocol + both clients + analyze/debate at 4096 → Tasks 1, 2 ✓
- §2 bounded context (recent 3yr/4q facts, 1200-char sections, ≤6 events / 500-char excerpts, prompt-only) → Task 3 ✓
- §3 `LLMResponseError` + graceful CLI exit → Tasks 2, 4 ✓
- §4 offline tests (max_tokens threading, context bounding, graceful failure) → Tasks 1–4; Task 5 verifies ✓

**Placeholder scan:** No TBD/"handle edge cases"/"similar to". Complete code each step. ✓

**Type consistency:** `complete(..., max_tokens: int = 2000)` identical across base/anthropic/mock (Task 1) and called with `max_tokens=_MAX_OUTPUT_TOKENS` in analyze/debate (Task 2). `_MAX_OUTPUT_TOKENS=4096`, `_CTX_MAX_ANNUAL/_CTX_MAX_QUARTERS/_CTX_SECTION_CHARS/_CTX_MAX_EVENTS/_CTX_EVENT_CHARS` defined Task 2/3, used in `_company_context` (Task 3) and asserted in tests. `LLMResponseError` + `_parse(model_cls, raw, schema)` defined Task 2, used by analyze/debate, caught in cli.py (Task 4). `_select_context_facts`/`_fy_num`/`_q_sort` defined + used Task 3. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-llm-truncation-fix.md`. Recommended: subagent-driven (fresh subagent per task, two-stage review). 5 tasks.

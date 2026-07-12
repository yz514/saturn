# Guidance-Anchored Growth (Slice 1.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When management has disclosed forward revenue guidance (in the earnings-release/8-K exhibits Saturn ingests), use that grounded, forward figure as the driver model's growth input instead of the backward-looking trailing-trend CAGR.

**Architecture:** A workflow-time LLM step `extract_guidance` reads the filing text, and Saturn accepts the guidance ONLY if its verbatim quote is found in that text (anti-hallucination gate). When accepted, `run()` recomputes the driver model with `growth_override = guidance.implied_growth`. Ingestion stays LLM-free; stance/scenarios untouched.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-guidance-anchored-growth-design.md`.

**File map:**
- `saturn/models.py` — `Guidance` + `DriverModel.growth_source`/`growth_citation` (Task 1)
- `saturn/analytics/driver.py` — `growth_override` param (Task 2)
- `saturn/agents/guidance.py` (NEW) — `extract_guidance` (Task 3)
- `saturn/workflows/equity_research.py` — run() wiring + `_company_context` note (Task 4)
- `saturn/reports/markdown_report.py` — Driver Bridge growth-source annotation + citation (Task 5)

---

### Task 1: `Guidance` model + `DriverModel` growth-source fields

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models_driver.py`

- [ ] **Step 1: Write the failing test** — APPEND to `tests/test_models_driver.py`:

```python
def test_guidance_model():
    from saturn.models import Guidance, Provenance
    g = Guidance(period="FY", value=70e9, implied_growth=0.15,
                 quote="We expect FY revenue of ~$70B.", provenance=Provenance(source="SEC EDGAR (guidance)"))
    assert g.metric == "revenue" and g.period == "FY" and abs(g.implied_growth - 0.15) < 1e-9


def test_driver_model_growth_source_defaults_to_trend():
    from saturn.models import DriverModel, Provenance
    dm = DriverModel(saturn_eps=2.0, trailing_revenue_growth=0.1, trailing_net_margin=0.1, shares=50.0,
                     provenance=Provenance(source="Saturn (model)"))
    assert dm.growth_source == "trend" and dm.growth_citation == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q -k "guidance or growth_source"`
Expected: FAIL (`cannot import name 'Guidance'`; `growth_source` attribute missing).

- [ ] **Step 3: Implement** — in `saturn/models.py`:

Add two fields to `class DriverModel`, immediately after its `caveats` field (before `provenance`):
```python
    growth_source: str = "trend"     # "trend" | "guidance"
    growth_citation: str = ""        # verbatim guidance quote when growth_source == "guidance"
```

Add this class immediately AFTER `class DriverModel` (before `class CompanyDossier`):
```python
class Guidance(BaseModel):
    """Management's disclosed forward revenue guidance, extracted from filing text and grounded
    (the quote must appear verbatim in the source). Feeds the driver model's growth input."""
    metric: str = "revenue"
    period: str                      # "FY" | "quarter"
    value: float                     # guided revenue figure (absolute, reported-Revenues scale)
    implied_growth: float            # computed vs TTM revenue
    quote: str                       # verbatim sentence from the filing
    provenance: Provenance
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_driver.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_driver.py
git commit -m "feat(models): Guidance + DriverModel growth_source/growth_citation"
```
Commit trailer (all commits): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: `compute_driver_model` growth_override

**Files:**
- Modify: `saturn/analytics/driver.py`
- Test: `tests/analytics/test_driver.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/analytics/test_driver.py`:

```python
def test_driver_growth_override_uses_guidance_growth():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None, growth_override=0.15)
    assert abs(dm.trailing_revenue_growth - 0.15) < 1e-9
    assert dm.growth_source == "guidance"
    exp = 1000 * 1.15 * 0.10 / 50
    assert abs(dm.saturn_eps - exp) < 1e-9


def test_driver_growth_override_suppresses_no_history_caveat():
    rows = [("Revenues", "FY2025", 1000.0),  # no FY2022 -> no trailing CAGR
            ("NetIncomeLoss", "FY2025", 100.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None, growth_override=0.12)
    assert dm.trailing_revenue_growth == 0.12 and dm.growth_source == "guidance"
    assert not any("no 3-year revenue history" in c for c in dm.caveats)


def test_driver_without_override_is_trend():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None)
    assert dm.growth_source == "trend"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q -k "override or is_trend"`
Expected: FAIL (`compute_driver_model() got an unexpected keyword argument 'growth_override'`).

- [ ] **Step 3: Implement** — in `saturn/analytics/driver.py`.

Change the signature:
```python
def compute_driver_model(fundamentals, quote, consensus, *, growth_override: float | None = None) -> DriverModel | None:
```

Replace the growth block. Current:
```python
    caveats: list[str] = []
    low_conf = False
    g = _revenue_cagr_3y(idx, latest_fy)
    if g is None:
        g = 0.0
        caveats.append("no 3-year revenue history; growth assumed 0%")
        low_conf = True
```
with:
```python
    caveats: list[str] = []
    low_conf = False
    if growth_override is not None:
        g = growth_override
        growth_source = "guidance"
    else:
        growth_source = "trend"
        g = _revenue_cagr_3y(idx, latest_fy)
        if g is None:
            g = 0.0
            caveats.append("no 3-year revenue history; growth assumed 0%")
            low_conf = True
```

In the `return DriverModel(...)` call, add the field (anywhere among the kwargs, e.g. after `low_confidence`/`caveats`):
```python
        growth_source=growth_source,
```
(`growth_citation` keeps its default `""`; the workflow stamps it.)

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_driver.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/driver.py tests/analytics/test_driver.py
git commit -m "feat(driver): growth_override to anchor the bridge on guidance instead of trend"
```

---

### Task 3: `extract_guidance` agent

**Files:**
- Create: `saturn/agents/guidance.py`
- Test: `tests/agents/test_guidance.py` (NEW)

- [ ] **Step 1: Write the failing tests** — create `tests/agents/test_guidance.py`:

```python
from datetime import date

from saturn.agents.guidance import extract_guidance
from saturn.models import CompanyDossier, FilingSection, FinancialFact, Fundamentals, Provenance

PROV = Provenance(source="SEC EDGAR")
_QUOTE = "We expect full-year revenue of approximately $70 billion."


def _dossier(quote_in_filing=True, rev=60_000_000_000.0):
    text = ("Some intro. " + _QUOTE + " More text.") if quote_in_filing else "No guidance here."
    return CompanyDossier(
        ticker="X", name="X", generated_at=date(2026, 7, 12),
        fundamentals=Fundamentals(facts=[
            FinancialFact(concept="Revenues", value=rev, unit="USD", fiscal_period="FY2025", provenance=PROV)]),
        filing_sections=[FilingSection(name="Earnings release", excerpt=text, provenance=PROV)])


class _GuidanceLLM:
    def __init__(self, payload):
        self.payload = payload
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=guidance" in prompt
        return self.payload


def _fy_payload():
    return '{"value": 70000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'


def test_extract_guidance_grounded_fy():
    g = extract_guidance(_dossier(), _GuidanceLLM(_fy_payload()))
    assert g is not None and g.period == "FY"
    assert abs(g.implied_growth - (70_000_000_000 / 60_000_000_000 - 1)) < 1e-9  # ~0.1667


def test_extract_guidance_quarter_annualized():
    payload = '{"value": 20000000000, "period": "quarter", "quote": "We expect full-year revenue of approximately $70 billion."}'
    g = extract_guidance(_dossier(), _GuidanceLLM(payload))
    # quarter -> value*4 = 80B vs 60B TTM -> +0.333
    assert g is not None and abs(g.implied_growth - (80_000_000_000 / 60_000_000_000 - 1)) < 1e-9


def test_extract_guidance_ungrounded_quote_rejected():
    payload = '{"value": 70000000000, "period": "FY", "quote": "We expect revenue of $999 trillion."}'
    assert extract_guidance(_dossier(), _GuidanceLLM(payload)) is None


def test_extract_guidance_malformed_none():
    assert extract_guidance(_dossier(), _GuidanceLLM("not json")) is None


def test_extract_guidance_empty_object_none():
    assert extract_guidance(_dossier(), _GuidanceLLM("{}")) is None


def test_extract_guidance_absurd_growth_rejected():
    # value 700B vs 60B TTM -> +1066% -> out of bounds
    payload = '{"value": 700000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'
    assert extract_guidance(_dossier(), _GuidanceLLM(payload)) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_guidance.py -q`
Expected: FAIL (`ModuleNotFoundError: saturn.agents.guidance`).

- [ ] **Step 3: Implement** — create `saturn/agents/guidance.py`:

```python
"""Guidance extraction: read management's DISCLOSED forward revenue guidance from filing text.

The LLM extracts a STATED figure (not a forecast); Saturn accepts it only when the cited quote is
found verbatim in the ingested filing text, so a fabricated guide is discarded (falls back to the
trailing-trend baseline).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from saturn.models import Guidance, Provenance

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 2048
_GROWTH_BOUNDS = (-0.9, 2.0)   # discard an implied growth outside this (mis-scaled figure)

GUIDANCE_SYSTEM = (
    "You extract management's DISCLOSED forward revenue guidance from an earnings release / 8-K. "
    "Report only a figure management EXPLICITLY stated, with the verbatim sentence — do NOT infer "
    "or forecast. If there is no explicit forward revenue guidance, return an empty object {}."
)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def extract_guidance(dossier, llm, *, model: str | None = None) -> Guidance | None:
    """Return grounded forward revenue Guidance, or None (soft-fail / not disclosed / ungrounded)."""
    from saturn.workflows.equity_research import _extract_json
    from saturn.analytics.metrics import _index, _ttm_or_fy
    try:
        source = " ".join((s.excerpt or "") for s in dossier.filing_sections)
        if not source.strip():
            return None
        prompt = (
            "OUTPUT_SCHEMA=guidance\n"
            "FILING TEXT (earnings release / 8-K):\n" + source[:8000] + "\n\n"
            "Return ONLY: {\"value\": number (guided revenue, same scale as reported revenue, e.g. "
            "50000000000 for $50B), \"period\": \"FY\" or \"quarter\", \"quote\": \"verbatim sentence\"} "
            "or {} if no explicit forward revenue guidance."
        )
        strict = "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single JSON object."
        data = None
        for attempt in range(2):
            raw = llm.complete(GUIDANCE_SYSTEM, prompt if attempt == 0 else prompt + strict,
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                data = json.loads(_extract_json(raw))
                break
            except Exception:  # noqa: BLE001 - malformed JSON; retry once
                continue
        if not isinstance(data, dict) or "value" not in data or "quote" not in data:
            return None

        value = float(data["value"])
        quote = str(data.get("quote") or "")
        period = "quarter" if str(data.get("period", "FY")).lower().startswith("q") else "FY"

        # grounding gate: the verbatim quote must appear in the ingested filing text
        if not quote or _norm(quote) not in _norm(source):
            logger.info("guidance discarded (quote not grounded) for %s", getattr(dossier, "ticker", "?"))
            return None

        rev = _ttm_or_fy(_index(dossier.fundamentals), "Revenues")
        if rev is None or rev[0] <= 0:
            return None
        base = value * 4 if period == "quarter" else value
        implied_growth = base / rev[0] - 1
        if not (_GROWTH_BOUNDS[0] <= implied_growth <= _GROWTH_BOUNDS[1]):
            logger.info("guidance discarded (implied growth %.2f out of bounds) for %s",
                        implied_growth, getattr(dossier, "ticker", "?"))
            return None

        return Guidance(metric="revenue", period=period, value=value, implied_growth=implied_growth,
                        quote=quote, provenance=Provenance(source="SEC EDGAR (guidance)", as_of=date.today()))
    except Exception as exc:  # noqa: BLE001 - guidance is best-effort; never breaks the report
        logger.warning("guidance extraction unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_guidance.py -q` → PASS (6 tests).
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/guidance.py tests/agents/test_guidance.py
git commit -m "feat(guidance): extract_guidance — grounded forward revenue guidance from filings"
```

---

### Task 4: Wire into `run()` + agent context

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/test_equity_research_workflow.py`:

```python
def _guidance_dossier():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.models import FilingSection, Provenance
    d = _mock_dossier("MSFT")
    d.filing_sections = list(d.filing_sections) + [FilingSection(
        name="Earnings release", excerpt="We expect full-year revenue of approximately $70 billion.",
        provenance=Provenance(source="SEC EDGAR"))]
    return d


class _GuidanceRunLLM:
    """Returns grounded revenue guidance; everything else is minimal valid JSON."""
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=guidance" in prompt:
            return '{"value": 70000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "o" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 0, "summary": "s", "findings": []})
        return "{}"


def test_run_uses_guidance_growth_when_grounded():
    r = run(_guidance_dossier(), _GuidanceRunLLM(), model_used="m", mock=False)
    assert r.company.driver_model is not None
    assert r.company.driver_model.growth_source == "guidance"
    assert "70 billion" in r.company.driver_model.growth_citation


def test_run_falls_back_to_trend_without_guidance():
    # MockLLMClient returns "{}" for the guidance prompt -> no guidance -> trend model retained
    r = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert r.company.driver_model is not None
    assert r.company.driver_model.growth_source == "trend"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q -k "guidance or falls_back_to_trend"`
Expected: FAIL (`growth_source` is always "trend"; run() doesn't extract guidance yet).

- [ ] **Step 3: Implement** — in `saturn/workflows/equity_research.py`.

Add imports near the top (with the other agent imports):
```python
from saturn.agents.guidance import extract_guidance
from saturn.analytics.driver import compute_driver_model
```

In `run()`, insert BEFORE `analysis = analyze(company, llm, model=call_model)`:
```python
    guidance = extract_guidance(company, llm, model=call_model)
    if guidance is not None:
        company.driver_model = compute_driver_model(
            company.fundamentals, company.quote, company.consensus,
            growth_override=guidance.implied_growth,
        )
        if company.driver_model is not None:
            company.driver_model.growth_citation = guidance.quote
```

In `_company_context`, in the DRIVER MODEL block, replace the Saturn-EPS line:
```python
        lines.append(f"- Saturn forward EPS ({dm.horizon}): {dm.saturn_eps:.2f} "
                     f"(rev growth {dm.trailing_revenue_growth:+.1%}, net margin {dm.trailing_net_margin:.1%})")
```
with (annotate the growth source + include the citation):
```python
        _src = "management guidance" if dm.growth_source == "guidance" else "trailing trend"
        lines.append(f"- Saturn forward EPS ({dm.horizon}): {dm.saturn_eps:.2f} "
                     f"(rev growth {dm.trailing_revenue_growth:+.1%} [{_src}], net margin {dm.trailing_net_margin:.1%})")
        if dm.growth_citation:
            lines.append(f'  guidance: "{dm.growth_citation}"')
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q` → PASS (existing run/self-repair/alpha-repair tests stay green — their stubs return "{}" for the guidance prompt → trend fallback).
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): extract guidance and re-anchor the driver model + context note"
```

---

### Task 5: Driver Bridge growth-source annotation + citation

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests** — APPEND to `tests/test_markdown_report.py`:

```python
def test_render_driver_bridge_guidance_source_and_citation():
    report = _sample_report()
    dm = _driver_model()
    dm.growth_source = "guidance"
    dm.growth_citation = "We expect full-year revenue of approximately $70 billion."
    report.company.driver_model = dm
    md = render(report)
    assert "(per management guidance)" in md
    assert "We expect full-year revenue of approximately $70 billion." in md


def test_render_driver_bridge_trend_source_label():
    report = _sample_report()
    report.company.driver_model = _driver_model()   # growth_source defaults to "trend"
    assert "(trailing trend)" in render(report)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q -k "driver_bridge_guidance or trend_source"`
Expected: FAIL (no source label / citation rendered).

- [ ] **Step 3: Implement** — in `saturn/reports/markdown_report.py`, in `_render_driver_bridge`.

Replace the Saturn-EPS line:
```python
    out.append(f"- **Saturn forward EPS ({dm.horizon}):** ${dm.saturn_eps:,.2f} "
               f"(rev growth {dm.trailing_revenue_growth:+.1%}, net margin {dm.trailing_net_margin:.1%})")
```
with (annotate source):
```python
    src = " (per management guidance)" if dm.growth_source == "guidance" else " (trailing trend)"
    out.append(f"- **Saturn forward EPS ({dm.horizon}):** ${dm.saturn_eps:,.2f} "
               f"(rev growth {dm.trailing_revenue_growth:+.1%}{src}, net margin {dm.trailing_net_margin:.1%})")
```

Then, just before the `if dm.caveats:` line (near the end of the helper), add the citation line:
```python
    if dm.growth_citation:
        out.append(f'- _Guidance: "{dm.growth_citation}"_')
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): Driver Bridge annotates growth source + shows the guidance citation"
```

---

## Final verification (live)

Regenerate a report for a name that guides revenue and confirm end-to-end:

```bash
.venv/Scripts/python.exe -m saturn.cli research MSFT
```

In `reports/MSFT_<date>.md`:
1. If MSFT's latest earnings release disclosed revenue guidance, the `### Driver Bridge` shows
   `rev growth +X% (per management guidance)` with a `_Guidance: "..."_` line quoting the filing,
   and Saturn's forward EPS reflects the guided growth.
2. If no guidance is disclosed (or the quote can't be grounded), it cleanly shows
   `(trailing trend)` — the Slice-1 behaviour.
3. The stance line and scenario table are unchanged.

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §3 extract_guidance + grounding + implied_growth → Task 3; §4 models → Task 1; §5 growth_override → Task 2; §6 run() wiring → Task 4; §7 render/context → Tasks 4/5. §9 mock: no MockLLMClient change needed — its default `"{}"` yields no guidance → trend (covered by `test_run_falls_back_to_trend_without_guidance`).
- **Type consistency:** `extract_guidance(dossier, llm, *, model=None) -> Guidance | None`; `compute_driver_model(..., *, growth_override=None)`; `DriverModel.growth_source`/`growth_citation`; `Guidance.implied_growth`/`quote`. Used identically across tasks.
- **Guardrail:** the quote-grounding gate + absurd-growth bound are both in `extract_guidance` (Task 3), tested by the ungrounded/absurd cases; nothing lets an ungrounded figure reach the driver model.
- **No placeholders:** every step has complete code.

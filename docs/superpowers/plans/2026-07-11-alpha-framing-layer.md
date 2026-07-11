# Alpha-Framing Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured, auditable Alpha Thesis (consensus-delta anchor → directional variant view → observable falsifier → deterministic bull/base/bear scenario prices) to every Saturn report.

**Architecture:** A new `synthesist` agent runs `analyze → debate → SYNTHESIZE → critique → render`. The LLM supplies a stance + assumptions; deterministic code resolves the market-expectation anchor, computes scenario prices (`value × multiple`), and runs a completeness gate. The Critic gains an `unsupported_alpha_inference` audit. No alpha auto-repair in v1.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-11-alpha-framing-layer-design.md`.

**File map:**
- `saturn/models.py` — `ExpectationAnchor`, `ScenarioLeg`, `AlphaThesis`, `ResearchReport.alpha_thesis` (Task 1)
- `saturn/agents/synthesist.py` (NEW) — anchor resolver, scenario pricing, completeness gate, `synthesize` (Tasks 2–4)
- `saturn/llm/mock_client.py` — `OUTPUT_SCHEMA=alpha` branch (Task 5)
- `saturn/agents/critic.py` — thread alpha into `critique` + new category (Task 6)
- `saturn/workflows/equity_research.py` — wire `synthesize` into `run()` (Task 7)
- `saturn/reports/markdown_report.py` — render §2 + renumber §3–§19 (Task 8)

---

### Task 1: Data models

**Files:**
- Modify: `saturn/models.py`
- Test: `tests/test_models_alpha.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_alpha.py
from saturn.models import AlphaThesis, ExpectationAnchor, ScenarioLeg, Provenance


def _leg(**kw):
    base = dict(name="base", period="FY2027", driver="d", metric="EPS", metric_basis="adjusted",
                per_share_value=10.0, multiple=15.0, multiple_basis="P/E")
    base.update(kw)
    return ScenarioLeg(**base)


def test_scenario_leg_computed_fields_default_none():
    leg = _leg()
    assert leg.implied_price is None and leg.implied_return_pct is None


def test_alpha_thesis_defaults_allow_partial():
    # anchor + provenance required; LLM-supplied fields default so a partial parse still validates
    t = AlphaThesis(anchor=ExpectationAnchor(source="none", text="x", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert t.stance == "unclear" and t.variant == "" and t.scenarios == [] and t.incompleteness == []


def test_scenario_leg_rejects_bad_literal():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _leg(metric="revenue")   # not in [EPS, FCF/share, sales/share]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py -q`
Expected: FAIL (`ImportError: cannot import name 'AlphaThesis'`).

- [ ] **Step 3: Implement**

In `saturn/models.py`, add `Literal` to the typing import at the top:

```python
from typing import Literal
```

Add these classes after `IndustryContext` (before `CompanyDossier`):

```python
class ExpectationAnchor(BaseModel):
    """What the market is pricing in — the base the variant view is measured against."""
    source: Literal["consensus", "reverse_dcf_implied", "none"]
    metric: str | None = None
    period: str | None = None
    value: float | None = None
    unit: str | None = None
    text: str
    confidence: Literal["high", "medium", "low"]


class ScenarioLeg(BaseModel):
    """One bull/base/bear leg. The LLM supplies the assumption; code computes the price."""
    name: Literal["bull", "base", "bear"]
    period: str
    driver: str
    metric: Literal["EPS", "FCF/share", "sales/share"]
    metric_basis: Literal["GAAP", "non_GAAP", "adjusted", "cycle_normalized"]
    per_share_value: float
    multiple: float
    multiple_basis: Literal["P/E", "P/FCF", "P/S"]
    implied_price: float | None = None
    implied_return_pct: float | None = None


class AlphaThesis(BaseModel):
    """A tradeable variant view: anchor, stance, falsifier, and priced scenarios.
    LLM-supplied fields default so a partial LLM response still validates; the
    completeness gate flags the gaps."""
    anchor: ExpectationAnchor
    stance: Literal["above_expectations", "in_line", "below_expectations", "unclear"] = "unclear"
    variant: str = ""
    rationale: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    key_variable: str = ""
    falsifier: str = ""
    horizon: str = ""
    scenarios: list[ScenarioLeg] = Field(default_factory=list)
    incompleteness: list[str] = Field(default_factory=list)
    provenance: Provenance
```

In `class ResearchReport`, add the field after `critic_review`:

```python
    alpha_thesis: AlphaThesis | None = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_alpha.py
git commit -m "feat(models): ExpectationAnchor / ScenarioLeg / AlphaThesis + ResearchReport.alpha_thesis"
```

---

### Task 2: Anchor resolver + deterministic scenario pricing

**Files:**
- Create: `saturn/agents/synthesist.py`
- Test: `tests/agents/test_synthesist.py` (NEW)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_synthesist.py
from datetime import date

from saturn.agents.synthesist import _resolve_anchor, _price_scenarios
from saturn.models import (
    CompanyDossier, ConsensusSnapshot, DerivedMetric, Provenance, Quote, ScenarioLeg,
)


def _dossier(**kw):
    base = dict(ticker="MU", name="Micron", generated_at=date(2026, 7, 10))
    base.update(kw)
    return CompanyDossier(**base)


def _leg(name="base", value=10.0, mult=15.0):
    return ScenarioLeg(name=name, period="FY2027", driver="d", metric="EPS",
                       metric_basis="adjusted", per_share_value=value, multiple=mult, multiple_basis="P/E")


def test_resolve_anchor_prefers_consensus():
    d = _dossier(consensus=ConsensusSnapshot(forward_pe=6.5, target_mean=180.0, rating="buy",
                 n_analysts=30, provenance=Provenance(source="yfinance (estimate)")))
    a = _resolve_anchor(d)
    assert a.source == "consensus" and a.metric == "Forward P/E" and a.value == 6.5 and a.unit == "x"


def test_resolve_anchor_falls_back_to_reverse_dcf():
    d = _dossier(derived_metrics=[DerivedMetric(name="implied_fcf_growth", value=0.14, format="percent",
                 fiscal_period="model", formula="f", provenance=Provenance(source="Saturn (model)"))])
    a = _resolve_anchor(d)
    assert a.source == "reverse_dcf_implied" and a.value == 0.14 and "14%" in a.text


def test_resolve_anchor_none_when_no_data():
    a = _resolve_anchor(_dossier())
    assert a.source == "none" and a.confidence == "low"


def test_price_scenarios_computes_price_and_return():
    legs = _price_scenarios([_leg(value=12.0, mult=20.0)], quote_price=200.0)
    assert legs[0].implied_price == 240.0
    assert abs(legs[0].implied_return_pct - 0.20) < 1e-9


def test_price_scenarios_no_quote_leaves_return_none():
    legs = _price_scenarios([_leg(value=10.0, mult=15.0)], quote_price=None)
    assert legs[0].implied_price == 150.0 and legs[0].implied_return_pct is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q`
Expected: FAIL (`ModuleNotFoundError: saturn.agents.synthesist`).

- [ ] **Step 3: Implement**

Create `saturn/agents/synthesist.py`:

```python
"""The Synthesist: turns analysis + debate into a structured, auditable Alpha Thesis."""
from __future__ import annotations

import json
import logging

from saturn.models import AlphaThesis, CompanyDossier, ExpectationAnchor, Provenance, ScenarioLeg

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192


def _resolve_anchor(dossier: CompanyDossier) -> ExpectationAnchor:
    """Deterministic market-expectation anchor: consensus first, else reverse-DCF implied, else none."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence

    cons = dossier.consensus
    if cons is not None and any(v is not None for v in (cons.forward_pe, cons.forward_eps, cons.target_mean)):
        if cons.forward_pe is not None:
            metric, value, unit = "Forward P/E", cons.forward_pe, "x"
        elif cons.forward_eps is not None:
            metric, value, unit = "forward EPS", cons.forward_eps, "USD/share"
        else:
            metric, value, unit = "mean price target", cons.target_mean, "USD/share"
        parts: list[str] = []
        if cons.forward_pe is not None:
            parts.append(f"forward P/E {cons.forward_pe:.1f}x")
        if cons.target_mean is not None:
            up = f" ({cons.target_upside_pct:+.0%} vs price)" if cons.target_upside_pct is not None else ""
            parts.append(f"mean target ${cons.target_mean:,.0f}{up}")
        if cons.rating:
            parts.append(f"rating {cons.rating}")
        if cons.n_analysts:
            parts.append(f"{cons.n_analysts} analysts")
        text = "Consensus: " + ", ".join(parts) + "." if parts else "Consensus estimates available."
        return ExpectationAnchor(source="consensus", metric=metric, period="NTM", value=value,
                                 unit=unit, text=text, confidence="low" if cons.rejected else "medium")

    fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
    implied = next((m for m in fwd if m.name == "implied_fcf_growth"), None)
    if implied is not None:
        low = is_reverse_dcf_low_confidence(fwd)
        note = " (LOW CONFIDENCE — trailing FCF base likely cycle-depressed)" if low else ""
        return ExpectationAnchor(source="reverse_dcf_implied", metric="implied FCF growth",
                                 period="perpetual", value=implied.value, unit="%",
                                 text=f"Price implies ~{implied.value:.0%} FCF growth{note}.",
                                 confidence="low" if low else "medium")

    return ExpectationAnchor(source="none", text="No market-expectation anchor available this run.",
                             confidence="low")


def _price_scenarios(legs: list[ScenarioLeg], quote_price: float | None) -> list[ScenarioLeg]:
    """Compute implied_price = per_share_value × multiple and return vs the current quote."""
    out: list[ScenarioLeg] = []
    for leg in legs:
        price = leg.per_share_value * leg.multiple
        ret = (price / quote_price - 1) if (quote_price and quote_price > 0) else None
        out.append(leg.model_copy(update={"implied_price": price, "implied_return_pct": ret}))
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): deterministic anchor resolver + scenario pricing"
```

---

### Task 3: Completeness gate

**Files:**
- Modify: `saturn/agents/synthesist.py`
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing test** (append to `tests/agents/test_synthesist.py`)

```python
from saturn.agents.synthesist import alpha_completeness
from saturn.models import AlphaThesis, ExpectationAnchor


def _complete_thesis(**kw):
    base = dict(
        anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
        stance="above_expectations", variant="Market underrates HBM margin durability.",
        rationale="r", confidence="medium", key_variable="HBM gross margin",
        falsifier="GM below 60% next 2 quarters", horizon="12-18 months",
        scenarios=[_leg("bull"), _leg("base"), _leg("bear")],
        provenance=Provenance(source="Saturn (synthesist)"))
    base.update(kw)
    return AlphaThesis(**base)


def test_completeness_complete_thesis_has_no_gaps():
    assert alpha_completeness(_complete_thesis()) == []


def test_completeness_flags_missing_pieces():
    gaps = alpha_completeness(_complete_thesis(falsifier="", scenarios=[_leg("bull"), _leg("base")]))
    assert any("falsifier" in g for g in gaps) and any("3 scenarios" in g for g in gaps)


def test_completeness_flags_none_anchor():
    t = _complete_thesis(anchor=ExpectationAnchor(source="none", text="x", confidence="low"))
    assert any("anchor" in g for g in alpha_completeness(t))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k completeness`
Expected: FAIL (`ImportError: cannot import name 'alpha_completeness'`).

- [ ] **Step 3: Implement** (append to `saturn/agents/synthesist.py`)

```python
def alpha_completeness(thesis: AlphaThesis) -> list[str]:
    """Structural-presence gaps only (semantic quality is the Critic's job). Returns
    human-readable gap strings; empty list means structurally complete."""
    gaps: list[str] = []
    if thesis.anchor.source == "none":
        gaps.append("no market-expectation anchor")
    if thesis.stance != "unclear" and not thesis.rationale.strip():
        gaps.append("stance without rationale")
    if not thesis.variant.strip():
        gaps.append("missing variant")
    elif len(thesis.variant.split()) > 50:
        gaps.append("variant too long (>50 words)")
    if not thesis.key_variable.strip():
        gaps.append("missing key variable")
    if not thesis.falsifier.strip():
        gaps.append("missing falsifier")
    if not thesis.horizon.strip():
        gaps.append("missing horizon")
    if len(thesis.scenarios) < 3:
        gaps.append("fewer than 3 scenarios")
    for s in thesis.scenarios:
        if not s.period.strip():
            gaps.append(f"scenario '{s.name}' missing period")
    return gaps
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q`
Expected: PASS (8 tests total in file).

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): deterministic alpha completeness gate"
```

---

### Task 4: `synthesize` agent

**Files:**
- Modify: `saturn/agents/synthesist.py`
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import json as _json
from saturn.agents.synthesist import synthesize


def _valid_alpha_json():
    return _json.dumps({
        "stance": "above_expectations", "variant": "Market underrates HBM margin durability.",
        "rationale": "SCAs lock demand.", "confidence": "medium", "key_variable": "HBM gross margin",
        "falsifier": "GM below 60% within 2 quarters", "horizon": "12-18 months",
        "scenarios": [
            {"name": "bull", "period": "FY2027", "driver": "HBM scarcity persists", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 13.0, "multiple": 18.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "normalizing", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "oversupply", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 10.0, "multiple_basis": "P/E"}]})


class _AlphaLLM:
    def __init__(self, payload): self.payload = payload
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=alpha" in prompt
        return self.payload


def _dossier_with_quote():
    return _dossier(quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
                    consensus=ConsensusSnapshot(forward_pe=6.5, provenance=Provenance(source="yfinance (estimate)")))


def _analysis():
    from saturn.models import AnalysisSections
    return AnalysisSections(executive_summary="e", company_overview="o", business_segments="s",
        financial_snapshot="f", valuation_discussion="v", key_risks="r", open_questions="q")


def _debate():
    from saturn.models import DebateSections
    return DebateSections(bull_thesis="b", bear_thesis="be", final_view="fv")


def test_synthesize_builds_priced_thesis():
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_valid_alpha_json()))
    assert t is not None and t.stance == "above_expectations" and len(t.scenarios) == 3
    base = next(s for s in t.scenarios if s.name == "base")
    assert base.implied_price == 150.0 and t.anchor.source == "consensus"
    assert t.incompleteness == []            # complete
    assert t.provenance.source == "Saturn (synthesist)"


def test_synthesize_malformed_soft_fails_to_none():
    assert synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM("not json")) is None


def test_synthesize_drops_bad_leg_keeps_rest():
    bad = _json.loads(_valid_alpha_json())
    bad["scenarios"][0]["metric"] = "revenue"     # invalid literal -> that leg dropped
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_json.dumps(bad)))
    assert len(t.scenarios) == 2 and any("3 scenarios" in g for g in t.incompleteness)


def test_synthesize_sanitizes_bad_stance():
    d = _json.loads(_valid_alpha_json())
    d["stance"] = "STRONG BUY"                     # not a valid literal -> coerced to unclear
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_json.dumps(d)))
    assert t.stance == "unclear"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k synthesize`
Expected: FAIL (`ImportError: cannot import name 'synthesize'`).

- [ ] **Step 3: Implement** (append to `saturn/agents/synthesist.py`)

```python
SYNTHESIZE_SYSTEM = (
    "You are a portfolio manager turning an analyst's memo into a tradeable view. You are given "
    "the market-expectation ANCHOR, the draft report, and the underlying data. State whether the "
    "view is above / in line with / below the anchor and WHY, grounded in specific data. If you "
    "cannot honestly take a differentiated view, return stance 'unclear' — never manufacture one. "
    "Give the single key variable that decides it, an OBSERVABLE falsifier (a concrete event plus a "
    "time window), a horizon, and exactly three scenarios (bull/base/bear). Each scenario states a "
    "period, a per-share metric with its value and basis, and a multiple with its basis — do NOT "
    "output prices; the system computes price = value x multiple. Keep 'variant' to ONE sentence "
    "under 35 words. Respond with ONLY a single valid JSON object, no prose, no code fences."
)


def _synthesize_prompt(analysis, debate, anchor: ExpectationAnchor, context: str) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    return (
        "OUTPUT_SCHEMA=alpha\n"
        f"MARKET-EXPECTATION ANCHOR ({anchor.source}): {anchor.text}\n\n"
        "DRAFT REPORT:\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n\n"
        "Return ONLY: {\"stance\": str, \"variant\": str, \"rationale\": str, \"confidence\": str, "
        "\"key_variable\": str, \"falsifier\": str, \"horizon\": str, \"scenarios\": "
        "[{\"name\": str, \"period\": str, \"driver\": str, \"metric\": str, \"metric_basis\": str, "
        "\"per_share_value\": number, \"multiple\": number, \"multiple_basis\": str}]}. "
        "stance in [above_expectations, in_line, below_expectations, unclear] RELATIVE TO THE ANCHOR. "
        "confidence in [high, medium, low]. name in [bull, base, bear] (exactly 3). "
        "metric in [EPS, FCF/share, sales/share]; metric_basis in [GAAP, non_GAAP, adjusted, cycle_normalized]; "
        "multiple_basis in [P/E, P/FCF, P/S]. Do NOT output prices."
    )


def _one_of(value, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _build_thesis(data: dict, anchor: ExpectationAnchor, dossier: CompanyDossier) -> AlphaThesis:
    legs: list[ScenarioLeg] = []
    for raw in (data.get("scenarios") or []):
        try:
            legs.append(ScenarioLeg.model_validate(raw))
        except Exception:  # noqa: BLE001 - drop a single malformed leg, keep the rest
            continue
    quote_price = dossier.quote.price if dossier.quote else None
    legs = _price_scenarios(legs, quote_price)
    thesis = AlphaThesis(
        anchor=anchor,
        stance=_one_of(data.get("stance"), ("above_expectations", "in_line", "below_expectations", "unclear"), "unclear"),
        variant=str(data.get("variant") or ""),
        rationale=str(data.get("rationale") or ""),
        confidence=_one_of(data.get("confidence"), ("high", "medium", "low"), "low"),
        key_variable=str(data.get("key_variable") or ""),
        falsifier=str(data.get("falsifier") or ""),
        horizon=str(data.get("horizon") or ""),
        scenarios=legs,
        provenance=Provenance(source="Saturn (synthesist)"),
    )
    thesis.incompleteness = alpha_completeness(thesis)
    return thesis


def synthesize(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None) -> AlphaThesis | None:
    """Produce a structured AlphaThesis. Resilient to imperfect LLM JSON (one retry, per-leg
    validation, sanitized enums). Soft-fails to None; never breaks the report."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        anchor = _resolve_anchor(dossier)
        prompt = _synthesize_prompt(analysis, debate, anchor, _company_context(dossier))
        strict = "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single, strictly valid JSON object."
        for attempt in range(2):
            raw = llm.complete(SYNTHESIZE_SYSTEM, prompt if attempt == 0 else prompt + strict,
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                return _build_thesis(json.loads(_extract_json(raw)), anchor, dossier)
            except Exception:  # noqa: BLE001 - malformed JSON; retry once then give up
                continue
        logger.warning("synthesist unavailable for %s: JSON unparseable after retry", getattr(dossier, "ticker", "?"))
        return None
    except Exception as exc:  # noqa: BLE001 - synthesist is best-effort, never breaks the report
        logger.warning("synthesist unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q`
Expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): synthesize() — resilient AlphaThesis generation"
```

---

### Task 5: Mock LLM alpha branch

**Files:**
- Modify: `saturn/llm/mock_client.py`
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_synthesize_with_mock_client_renders():
    from saturn.llm.mock_client import MockLLMClient
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), MockLLMClient())
    assert t is not None and len(t.scenarios) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k mock_client`
Expected: FAIL (`AssertionError` — MockLLMClient returns `{}` for alpha, so `synthesize` builds a thesis with 0 scenarios).

- [ ] **Step 3: Implement**

In `saturn/llm/mock_client.py`, add the `_ALPHA` constant after `_CRITIC`:

```python
_ALPHA = json.dumps(
    {
        "stance": "in_line",
        "variant": "[MOCK] No differentiated view in offline mode.",
        "rationale": "[MOCK] Placeholder rationale.",
        "confidence": "low",
        "key_variable": "[MOCK] key variable",
        "falsifier": "[MOCK] observable event within 2 quarters",
        "horizon": "next 2 quarters",
        "scenarios": [
            {"name": "bull", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 13.0, "multiple": 18.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "[MOCK]", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 10.0, "multiple_basis": "P/E"},
        ],
    }
)
```

In `MockLLMClient.complete`, add this branch before the `OUTPUT_SCHEMA=critic` branch:

```python
        if "OUTPUT_SCHEMA=alpha" in prompt:
            return _ALPHA
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k mock_client`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/llm/mock_client.py tests/agents/test_synthesist.py
git commit -m "feat(mock): OUTPUT_SCHEMA=alpha branch so --mock renders the Alpha Thesis"
```

---

### Task 6: Critic audits the alpha thesis

**Files:**
- Modify: `saturn/agents/critic.py`
- Test: `tests/agents/test_critic.py`

- [ ] **Step 1: Write the failing test** (append to `tests/agents/test_critic.py`)

```python
# ---- Alpha-framing: critic audits the alpha thesis ----

from saturn.agents.critic import _critic_prompt
from saturn.models import AlphaThesis, ExpectationAnchor, ScenarioLeg


def _alpha():
    return AlphaThesis(
        anchor=ExpectationAnchor(source="consensus", text="fwd P/E 6.5x", confidence="medium"),
        stance="above_expectations", variant="Market underrates HBM durability.", rationale="r",
        confidence="medium", key_variable="HBM GM", falsifier="GM<60% in 2Q", horizon="12-18m",
        scenarios=[ScenarioLeg(name="base", period="FY2027", driver="d", metric="EPS",
                   metric_basis="adjusted", per_share_value=10.0, multiple=15.0, multiple_basis="P/E")],
        provenance=Provenance(source="Saturn (synthesist)"))


def test_critic_prompt_includes_alpha_and_new_category():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=_alpha())
    assert "unsupported_alpha_inference" in p
    assert "Market underrates HBM durability." in p        # alpha thesis text is in the scan


def test_critic_prompt_omits_alpha_when_none():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=None)
    assert "unsupported_alpha_inference" not in p


class _AlphaInferenceLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return ('{"claims_checked": 1, "summary": "s", "findings": ['
                '{"claim": "margins reflect upfront recognition", "section": "alpha_thesis",'
                ' "category": "unsupported_alpha_inference", "verdict": "unsupported",'
                ' "evidence": "no contract-liability growth supports it", "severity": "high"}]}')


def test_critique_keeps_unsupported_alpha_inference():
    review = critique(_analysis(), _debate(), _dossier(), _AlphaInferenceLLM(), alpha=_alpha())
    assert [f.category for f in review.findings] == ["unsupported_alpha_inference"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q -k "alpha"`
Expected: FAIL (`_critic_prompt() got an unexpected keyword argument 'alpha'`).

- [ ] **Step 3: Implement**

In `saturn/agents/critic.py`, replace `_critic_prompt` with a version taking `alpha`:

```python
def _alpha_text(alpha) -> str:
    """Flatten an AlphaThesis into text the Critic can scan."""
    legs = "; ".join(
        f"{s.name} {s.period}: {s.per_share_value:g} {s.metric} x {s.multiple:g} {s.multiple_basis} "
        f"(driver: {s.driver})" for s in alpha.scenarios)
    return (f"stance={alpha.stance} vs anchor [{alpha.anchor.source}: {alpha.anchor.text}]; "
            f"variant: {alpha.variant}; rationale: {alpha.rationale}; "
            f"key_variable: {alpha.key_variable}; falsifier: {alpha.falsifier}; "
            f"horizon: {alpha.horizon}; scenarios: {legs}")


def _critic_prompt(analysis, debate, context: str, low_conf: bool, alpha=None) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    if alpha is not None:
        sections["alpha_thesis"] = _alpha_text(alpha)
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    note = ("\nNOTE: the reverse-DCF is flagged LOW CONFIDENCE. Report over_weighting ONLY if "
            "the thesis RELIES on its fair value / margin of safety as a PRIMARY argument. If the "
            "report explicitly dismisses or caveats it (e.g. 'diagnostic only', 'not a primary "
            "lens'), that is CORRECT handling — do NOT flag it.\n" if low_conf else "")
    alpha_note = ("\nThe report includes an ALPHA THESIS. Also flag category "
                  "unsupported_alpha_inference when: the variant is not connected to the anchor; a "
                  "scenario driver has no support in the data; the falsifier is not an observable "
                  "event with a time window; or a conclusion is stronger than its evidence (e.g. an "
                  "accounting inference with no contract-liability / deferred-revenue / filing "
                  "support).\n" if alpha is not None else "")
    categories = ("[unsupported_number, contradiction, over_weighting, unverified_claim"
                  + (", unsupported_alpha_inference]" if alpha is not None else "]"))
    return (
        "OUTPUT_SCHEMA=critic\n"
        "DRAFT REPORT (verify this prose):\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n" + note + alpha_note +
        "\nReturn ONLY: {\"claims_checked\": int, \"summary\": str, \"findings\": "
        "[{\"claim\": str, \"section\": str, \"category\": str, \"verdict\": str, "
        "\"evidence\": str, \"severity\": str}]}. category in " + categories + "."
    )
```

Update `critique` to accept and pass `alpha`. Change its signature and the `_critic_prompt` call:

```python
def critique(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None, alpha=None) -> CriticReview | None:
```

and inside, the prompt line:

```python
        prompt = _critic_prompt(analysis, debate, _company_context(dossier),
                                is_reverse_dcf_low_confidence(fwd), alpha)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/critic.py tests/agents/test_critic.py
git commit -m "feat(critic): audit the alpha thesis (unsupported_alpha_inference, L4)"
```

---

### Task 7: Wire synthesize into the pipeline

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_equity_research_workflow.py`)

```python
def test_run_populates_alpha_thesis():
    r = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert r.alpha_thesis is not None and len(r.alpha_thesis.scenarios) == 3
    base = next(s for s in r.alpha_thesis.scenarios if s.name == "base")
    assert base.implied_price == 150.0            # 10 × 15 from the mock
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q -k alpha`
Expected: FAIL (`AttributeError: 'ResearchReport' object has no attribute 'alpha_thesis'` is already added in Task 1, so instead FAIL is `assert None is not None`).

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py`, add the import near the critic import:

```python
from saturn.agents.synthesist import synthesize
```

In `run()`, insert the synthesize call after `deb = debate(...)` and pass `alpha` to BOTH `critique` calls and the report. The updated body:

```python
    call_model = None if mock else model_used
    analysis = analyze(company, llm, model=call_model)
    deb = debate(company, llm, model=call_model)
    alpha = synthesize(analysis, deb, company, llm, model=call_model)
    review = critique(analysis, deb, company, llm, model=call_model, alpha=alpha)

    if review is not None and _actionable(review):
        corrections = revise(analysis, deb, review, company, llm, model=call_model)
        if corrections:
            r_analysis = analysis.model_copy(
                update={k: v for k, v in corrections.items() if k in AnalysisSections.model_fields})
            r_deb = deb.model_copy(
                update={k: v for k, v in corrections.items() if k in DebateSections.model_fields})
            r_review = critique(r_analysis, r_deb, company, llm, model=call_model, alpha=alpha)
            if r_review is not None and _score(r_review) < _score(review):
                r_review.repaired = True
                analysis, deb, review = r_analysis, r_deb, r_review

    return ResearchReport(
        ticker=company.ticker,
        company=company,
        analysis=analysis,
        debate=deb,
        generated_at=date.today(),
        model_used=model_used,
        mock=mock,
        sources=_build_sources(company, mock=mock),
        critic_review=review,
        alpha_thesis=alpha,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q`
Expected: PASS (existing self-repair tests still green; new alpha test passes).

- [ ] **Step 5: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): synthesize alpha thesis and thread it through critique + report"
```

---

### Task 8: Render §2 Alpha Thesis + renumber §3–§19

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_markdown_report.py`)

```python
def _alpha_thesis(incomplete=False):
    from saturn.models import AlphaThesis, ExpectationAnchor, ScenarioLeg, Provenance
    return AlphaThesis(
        anchor=ExpectationAnchor(source="consensus", text="forward P/E 6.5x", confidence="medium"),
        stance="above_expectations", variant="Market underrates HBM margin durability.",
        rationale="SCAs lock demand.", confidence="medium", key_variable="HBM gross margin",
        falsifier="GM below 60% within 2 quarters", horizon="12-18 months",
        scenarios=[ScenarioLeg(name="base", period="FY2027", driver="normalizing", metric="EPS",
                   metric_basis="adjusted", per_share_value=10.0, multiple=15.0, multiple_basis="P/E",
                   implied_price=150.0, implied_return_pct=0.5)],
        incompleteness=(["missing falsifier"] if incomplete else []),
        provenance=Provenance(source="Saturn (synthesist)"))


def test_render_alpha_thesis_section():
    report = _sample_report()
    report.alpha_thesis = _alpha_thesis()
    md = render(report)
    assert "## 2. Alpha Thesis" in md
    assert "Market underrates HBM margin durability." in md
    assert "| Scenario | Period | Driver | Math | Price | Return |" in md
    assert "$150.00" in md and "GM below 60% within 2 quarters" in md


def test_render_alpha_incomplete_label():
    report = _sample_report()
    report.alpha_thesis = _alpha_thesis(incomplete=True)
    assert "## 2. Alpha Thesis (Incomplete — low confidence)" in render(report)


def test_render_alpha_unavailable():
    report = _sample_report()
    report.alpha_thesis = None
    assert "_Alpha thesis unavailable this run._" in render(report)
```

Also update `test_render_has_all_sections` (the list around line 62) to the new numbering and insert Alpha Thesis:

```python
    for header in [
        "## 1. Executive Summary",
        "## 2. Alpha Thesis",
        "## 3. Company Overview",
        "## 4. Business Segments",
        "## 5. Recent Market Performance",
        "## 6. Financial Snapshot",
        "## 7. Key Metrics",
        "## 8. Recent News and Catalysts",
        "## 9. Bull Thesis",
        "## 10. Bear Thesis",
        "## 11. Key Risks",
        "## 12. Valuation Discussion",
        "## 13. Open Questions",
        "## 14. Final View",
        "## 15. Verification (Critic)",
        "## 16. Macro Snapshot",
        "## 17. Material Events (SEC 8-K)",
        "## 18. Sources",
    ]:
```

Update the other number-bearing assertions in this file:
- `"## 6. Key Metrics"` → `"## 7. Key Metrics"` (test_render_key_metrics_section)
- `"## 18. Data Gaps"` → `"## 19. Data Gaps"` (test_render_shows_data_gaps_section)
- `"## 16. Material Events (SEC 8-K)"` → `"## 17. Material Events (SEC 8-K)"`
- `"## 17. Sources"` → `"## 18. Sources"`
- `_section7` helper: `md.split("## 7. Recent News and Catalysts")[1].split("## 8.")[0]` → `md.split("## 8. Recent News and Catalysts")[1].split("## 9.")[0]`

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q`
Expected: FAIL (missing `## 2. Alpha Thesis`, and renumbered headers not yet emitted).

- [ ] **Step 3: Implement**

In `saturn/reports/markdown_report.py`, add the render helper before `def render(`:

```python
def _render_alpha(thesis) -> list[str]:
    suffix = " (Incomplete — low confidence)" if thesis.incompleteness else ""
    out: list[str] = [f"## 2. Alpha Thesis{suffix}", ""]
    a = thesis.anchor
    out.append(f"**Anchor** ({a.source}): {a.text}")
    out.append("")
    out.append(f"**Stance:** {thesis.stance.replace('_', ' ')} · confidence {thesis.confidence}")
    out.append("")
    if thesis.variant:
        out += [f"**Variant perception:** {thesis.variant}", ""]
    if thesis.rationale:
        out += [f"**Rationale:** {thesis.rationale}", ""]
    out.append(f"**Key variable:** {thesis.key_variable or 'N/A'}")
    out.append(f"**Falsifier:** {thesis.falsifier or 'N/A'}")
    out.append(f"**Horizon:** {thesis.horizon or 'N/A'}")
    out.append("")
    if thesis.scenarios:
        out.append("| Scenario | Period | Driver | Math | Price | Return |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for s in thesis.scenarios:
            math = f"{s.per_share_value:g} {s.metric} × {s.multiple:g} {s.multiple_basis}"
            price = f"${s.implied_price:,.2f}" if s.implied_price is not None else "N/A"
            ret = f"{s.implied_return_pct:+.0%}" if s.implied_return_pct is not None else "N/A"
            out.append(f"| {s.name} | {s.period} | {s.driver} | {math} | {price} | {ret} |")
        out.append("")
    if thesis.incompleteness:
        out += [f"_Alpha thesis incomplete: {', '.join(thesis.incompleteness)}._", ""]
    return out
```

In `render()`, immediately after the Executive Summary line
(`out += ["## 1. Executive Summary", "", a.executive_summary, ""]`), insert:

```python
    if report.alpha_thesis is not None:
        out += _render_alpha(report.alpha_thesis)
    else:
        out += ["## 2. Alpha Thesis", "", "_Alpha thesis unavailable this run._", ""]
```

Then renumber every subsequent section header literal in `render()` (and the Critic section title inside it). Apply these exact replacements:

- `"## 2. Company Overview"` → `"## 3. Company Overview"`
- `"## 3. Business Segments"` → `"## 4. Business Segments"`
- `"## 4. Recent Market Performance"` → `"## 5. Recent Market Performance"`
- `"## 5. Financial Snapshot"` → `"## 6. Financial Snapshot"`
- `"## 6. Key Metrics"` → `"## 7. Key Metrics"`
- `"## 7. Recent News and Catalysts"` → `"## 8. Recent News and Catalysts"`
- `"## 8. Bull Thesis"` → `"## 9. Bull Thesis"`
- `"## 9. Bear Thesis"` → `"## 10. Bear Thesis"`
- `"## 10. Key Risks"` → `"## 11. Key Risks"`
- `"## 11. Valuation Discussion"` → `"## 12. Valuation Discussion"`
- `"## 12. Open Questions"` → `"## 13. Open Questions"`
- `"## 13. Final View"` → `"## 14. Final View"`
- `"## 14. Verification (Critic)"` → `"## 15. Verification (Critic)"`
- `"## 15. Macro Snapshot"` → `"## 16. Macro Snapshot"`
- `"## 16. Material Events (SEC 8-K)"` → `"## 17. Material Events (SEC 8-K)"`
- `"## 17. Sources"` → `"## 18. Sources"`
- `"## 18. Data Gaps"` → `"## 19. Data Gaps"`

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q`
Expected: PASS.

- [ ] **Step 5: Guard against stray section-number assertions elsewhere**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (full suite). If any other test asserts an old section number (e.g. in `tests/test_cli.py`), update it to the new numbering with the same old→new mapping above.

- [ ] **Step 6: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): render §2 Alpha Thesis + renumber sections 3–19"
```

---

## Final verification (live)

After all tasks pass, regenerate a real report and eyeball the new section:

```bash
.venv/Scripts/python.exe -m saturn.cli research MU
```

Confirm in `reports/MU_<date>.md`:
1. **§2 Alpha Thesis** states an **anchor** (consensus or reverse-DCF implied), a one-line **variant** vs. that anchor, a **key variable**, an **observable falsifier** with a horizon, and a **scenario table** whose Price column equals `per_share_value × multiple`.
2. If any element is missing, the header shows **"(Incomplete — low confidence)"**.
3. **Validate the Critic's `unsupported_alpha_inference` against the real LLM** (per the standing lesson — the mock can't): run a probe like the Critic-v2 live check, feeding a deliberately over-reaching alpha thesis (e.g. an accounting inference with no contract-liability support) and confirm the real Critic flags it. Note findings; do NOT tune the instruction on a single sample.

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §3 models → Task 1; §4 anchor → Task 2; §6 pricing → Task 2; §7 gate → Task 3; §5 synthesize → Tasks 4–5; §8 Critic L4/L5 → Tasks 3 (gate=L5) + 6 (L4); §9 render → Task 8; §10 edges → Tasks 4/5/8; §2 flow → Task 7. All covered.
- **Deferred (per spec §13):** no alpha auto-repair (Task 7 passes the same `alpha` to the re-critique but never rewrites it); per-share-only pricing; prose falsifier.
- **Type consistency:** `synthesize(analysis, debate, dossier, llm, *, model=None)`, `critique(..., alpha=None)`, `_resolve_anchor(dossier) -> ExpectationAnchor`, `_price_scenarios(legs, quote_price)`, `alpha_completeness(thesis) -> list[str]` — used identically across tasks. `Provenance(source="Saturn (synthesist)")` consistent.

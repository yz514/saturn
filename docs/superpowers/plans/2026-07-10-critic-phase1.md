# The Critic (Phase 1, advisory) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** An advisory verification agent that reads the drafted report prose against the provenance-tagged dossier and surfaces unsupported numbers, internal contradictions, and over-weighting of a low-confidence signal. Non-blocking, soft-fail.

**Design:** `docs/superpowers/specs/2026-07-10-critic-phase1-design.md`

---

### Task 1: Data model

**Files:** Modify `saturn/models.py`; Test `tests/test_models.py` (create if absent, else add).

- [ ] **Step 1: failing test**
```python
def test_critic_review_model():
    from saturn.models import CriticFinding, CriticReview, Provenance
    r = CriticReview(
        findings=[CriticFinding(claim="Cloud is fastest-growing", section="business_segments",
                                category="contradiction", verdict="contradicted",
                                evidence="table shows Core DC +653% > Cloud +307%", severity="high")],
        claims_checked=12, summary="1 contradiction found.",
        provenance=Provenance(source="Saturn (critic)"))
    assert r.findings[0].category == "contradiction" and r.claims_checked == 12
    assert CriticReview(provenance=Provenance(source="Saturn (critic)")).findings == []
```

- [ ] **Step 2: run → fail.** `.venv/Scripts/python.exe -m pytest tests/test_models.py -q -k critic_review`

- [ ] **Step 3: implement** in `saturn/models.py` (near `ResearchReport`):
```python
class CriticFinding(BaseModel):
    """One issue the Critic found: a report claim not supported by the data."""
    claim: str
    section: str
    category: str   # unsupported_number | contradiction | over_weighting | unverified_claim
    verdict: str    # contradicted | unsupported | flagged
    evidence: str
    severity: str   # high | medium | low


class CriticReview(BaseModel):
    """Advisory verification of the drafted report against the dossier."""
    findings: list[CriticFinding] = Field(default_factory=list)
    claims_checked: int = 0
    summary: str = ""
    provenance: Provenance
```
Add to `ResearchReport`: `critic_review: CriticReview | None = None`.

- [ ] **Step 4: run → pass; then full suite.**
- [ ] **Step 5: commit** `feat(models): CriticFinding / CriticReview + ResearchReport.critic_review`

---

### Task 2: Deterministic dollar-grounding helper

**Files:** Create `saturn/agents/__init__.py` (empty) + `saturn/agents/critic.py`; Test `tests/agents/test_critic.py`.

- [ ] **Step 1: failing test**
```python
from saturn.agents.critic import is_dollar_grounded
from saturn.models import CompanyDossier, FilingSection, FinancialFact, DerivedMetric, Provenance

def _dossier():
    return CompanyDossier(
        ticker="MU", name="Micron",
        fundamentals=None,
        derived_metrics=[DerivedMetric(name="revenue_ttm", value=90_274_000_000.0, format="currency",
                        fiscal_period="TTM", formula="f", provenance=Provenance(source="Saturn (derived)"))],
        filing_sections=[FilingSection(name="Business Unit / Segment Results (earnings release)",
                        excerpt="adjusted free cash flow was $18.3 billion", provenance=Provenance(source="SEC EDGAR"))],
    )

def test_dollar_grounded_matches_metric():
    assert is_dollar_grounded("$90.3B", _dossier()) is True     # ~ revenue_ttm

def test_dollar_grounded_matches_source_text():
    assert is_dollar_grounded("$18.3B", _dossier()) is True     # in the press-release excerpt

def test_dollar_not_grounded():
    assert is_dollar_grounded("$999B", _dossier()) is False
```
(Adjust `CompanyDossier(...)` kwargs to the model's required fields; keep it minimal.)

- [ ] **Step 2: run → fail.**

- [ ] **Step 3: implement** in `saturn/agents/critic.py`:
```python
"""The Critic: advisory verification of a drafted report against the dossier."""
from __future__ import annotations

import json
import logging
import re

from saturn.models import CompanyDossier, CriticReview

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192

_DOLLAR_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|[bmt])?\b", re.IGNORECASE)
_MULT = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6}


def _parse_dollar(token: str) -> float | None:
    m = _DOLLAR_RE.search(token or "")
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return num * _MULT.get((m.group(2) or "").lower(), 1.0)


def _dossier_values(dossier: CompanyDossier) -> list[float]:
    vals = [m.value for m in dossier.derived_metrics if m.value is not None]
    if dossier.fundamentals:
        vals += [f.value for f in dossier.fundamentals.facts if f.value is not None]
    if dossier.quote and dossier.quote.market_cap:
        vals.append(dossier.quote.market_cap)
    return vals


def is_dollar_grounded(token: str, dossier: CompanyDossier, *, tol: float = 0.02) -> bool:
    """True if a $-magnitude token matches a dossier fact/metric within `tol`, or its
    digits appear in the ingested filing/press-release source text."""
    v = _parse_dollar(token)
    if v is None or v == 0:
        return False
    for dv in _dossier_values(dossier):
        if dv and abs(v - dv) <= tol * abs(dv):
            return True
    digits = re.sub(r"[^\d.]", "", token)
    source = " ".join((s.excerpt or "") for s in dossier.filing_sections).replace(",", "")
    return bool(digits) and digits in source
```

- [ ] **Step 4: run → pass; full suite.**
- [ ] **Step 5: commit** `feat(critic): deterministic dollar-grounding helper`

---

### Task 3: `critique()` — LLM verification + soft-fail + backstop

**Files:** Modify `saturn/agents/critic.py`; Test `tests/agents/test_critic.py`.

- [ ] **Step 1: failing test**
```python
from saturn.agents.critic import critique
from saturn.models import AnalysisSections, DebateSections

def _analysis():
    return AnalysisSections(executive_summary="Fair value $16.34 is the key takeaway.",
        company_overview="o", business_segments="Cloud is the fastest-growing segment.",
        financial_snapshot="s", valuation_discussion="v", key_risks="r", open_questions="q")

def _debate():
    return DebateSections(bull_thesis="b", bear_thesis="Data shows margins of $999B somewhere.", final_view="f")

class _CriticLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=critic" in prompt
        return ('{"claims_checked": 5, "summary": "issues found", "findings": ['
                '{"claim": "$999B", "section": "bear_thesis", "category": "unsupported_number",'
                ' "verdict": "unsupported", "evidence": "not in data", "severity": "high"},'
                '{"claim": "$90.3B TTM revenue", "section": "financial_snapshot", "category": "unsupported_number",'
                ' "verdict": "unsupported", "evidence": "wrong", "severity": "low"}]}')

class _BrokenLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return "not json at all"

def test_critique_parses_and_applies_backstop():
    # dossier grounds $90.3B (revenue_ttm) but not $999B -> backstop drops the $90.3B false positive
    review = critique(_analysis(), _debate(), _dossier(), _CriticLLM())
    cats = [(f.claim, f.category) for f in review.findings]
    assert any("999" in c for c, _ in cats)                 # ungrounded kept
    assert not any("90.3" in c for c, _ in cats)            # grounded dropped by backstop
    assert review.provenance.source == "Saturn (critic)"

def test_critique_soft_fails_to_none():
    assert critique(_analysis(), _debate(), _dossier(), _BrokenLLM()) is None
```

- [ ] **Step 2: run → fail.**

- [ ] **Step 3: implement** in `saturn/agents/critic.py`:
```python
CRITIC_SYSTEM = (
    "You are a skeptical verification analyst. You are given a DRAFT equity research "
    "report and the UNDERLYING provenance-tagged data the analyst was given. Your ONLY "
    "job is to find where the report's claims are NOT supported by that data. Check: "
    "(1) quantitative factual claims not traceable to a provided datum/source "
    "(category unsupported_number); (2) internal contradictions — a statement conflicting "
    "with another statement or a table in the report (category contradiction); (3) whether "
    "the thesis leads with a signal flagged LOW CONFIDENCE (category over_weighting). "
    "Quote claims exactly. Do NOT invent issues; if a claim checks out, omit it. "
    "Respond with ONLY a valid JSON object, no prose, no code fences."
)


def _critic_prompt(analysis, debate, context: str, low_conf: bool) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    note = ("\nNOTE: the reverse-DCF is flagged LOW CONFIDENCE; if the thesis leads with its "
            "fair value or margin of safety, report it as category over_weighting.\n" if low_conf else "")
    return (
        "OUTPUT_SCHEMA=critic\n"
        "DRAFT REPORT (verify this prose):\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n" + note +
        "\nReturn ONLY: {\"claims_checked\": int, \"summary\": str, \"findings\": "
        "[{\"claim\": str, \"section\": str, \"category\": str, \"verdict\": str, "
        "\"evidence\": str, \"severity\": str}]}. category in "
        "[unsupported_number, contradiction, over_weighting, unverified_claim]."
    )


def critique(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None) -> CriticReview | None:
    """Advisory verification. Returns None (soft-fail) on any LLM/parse error."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
        prompt = _critic_prompt(analysis, debate, _company_context(dossier), is_reverse_dcf_low_confidence(fwd))
        raw = llm.complete(CRITIC_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
        data = json.loads(_extract_json(raw))
        data["provenance"] = {"source": "Saturn (critic)"}
        review = CriticReview.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - critic is advisory, never breaks the report
        logger.warning("critic unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
    # deterministic backstop: drop unsupported_number findings whose $ figure IS grounded
    review.findings = [
        f for f in review.findings
        if not (f.category == "unsupported_number" and is_dollar_grounded(f.claim, dossier))
    ]
    return review
```

- [ ] **Step 4: run → pass; full suite.**
- [ ] **Step 5: commit** `feat(critic): critique() LLM verification with soft-fail + grounding backstop`

---

### Task 4: Wire into run() + render Verification section + mock client

**Files:** Modify `saturn/workflows/equity_research.py`, `saturn/reports/markdown_report.py`, `saturn/llm/mock_client.py`; Test `tests/test_equity_research_workflow.py`, `tests/test_markdown_report.py`.

- [ ] **Step 1: failing tests**

In `tests/test_markdown_report.py`:
```python
def test_render_verification_section():
    from saturn.models import CriticReview, CriticFinding, Provenance
    report = _sample_report()
    report.critic_review = CriticReview(
        findings=[CriticFinding(claim="Cloud fastest-growing", section="business_segments",
                  category="contradiction", verdict="contradicted", evidence="Core DC +653%", severity="high")],
        claims_checked=9, summary="1 issue.", provenance=Provenance(source="Saturn (critic)"))
    md = render(report)
    assert "Verification (Critic)" in md and "contradiction" in md and "Cloud fastest-growing" in md

def test_render_verification_absent():
    report = _sample_report()
    report.critic_review = None
    assert "_Verification unavailable._" in render(report)
```
In `tests/test_equity_research_workflow.py`: assert `run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True).critic_review is not None`.

- [ ] **Step 2: run → fail.**

- [ ] **Step 3a: run() wiring** in `saturn/workflows/equity_research.py`:
Add import `from saturn.agents.critic import critique`. In `run()`, after `deb = debate(...)`:
```python
    review = critique(analysis, deb, company, llm, model=call_model)
```
and pass `critic_review=review` into `ResearchReport(...)`.

- [ ] **Step 3b: render** in `saturn/reports/markdown_report.py` — insert AFTER the Final View section (`## 13. Final View`) and BEFORE Macro, then renumber the three sections below it:
```python
    out += ["## 14. Verification (Critic)", ""]
    cr = report.critic_review
    if cr is None:
        out.append("_Verification unavailable._")
    elif not cr.findings:
        out.append(f"_No material discrepancies found against the underlying data ({cr.claims_checked} claims checked)._")
    else:
        out.append(f"{cr.summary} ({cr.claims_checked} claims checked)")
        out.append("")
        for f in cr.findings:
            out.append(f"- ⚠️ **{f.category}** [{f.section}, {f.severity}]: \"{f.claim}\" — {f.evidence}")
    out.append("")
```
Then change `## 14. Macro Snapshot` → `## 15.`, `## 15. Material Events (SEC 8-K)` → `## 16.`, `## 16. Sources` → `## 17.`. Update any test asserting the old numbers (`test_render_groups_financials_and_shows_events` asserts `## 15. Material Events` and `## 16. Sources` → now 16 / 17).

- [ ] **Step 3c: mock client** in `saturn/llm/mock_client.py`: add a `_CRITIC` constant and branch:
```python
_CRITIC = json.dumps({"claims_checked": 0, "summary": "[MOCK] verification placeholder.", "findings": []})
```
in `complete`, before the fallback: `if "OUTPUT_SCHEMA=critic" in prompt: return _CRITIC`.

- [ ] **Step 4: run → pass; full suite.** Fix any renumbering-related test assertions.
- [ ] **Step 5: commit** `feat(workflow): run the Critic after debate + render Verification section`

---

## Final verification (after all tasks)

Live (one LLM call): regenerate MU and inspect `## 14. Verification (Critic)` — confirm it
flags at least one real issue (a Cloud-vs-Core contradiction if the analyst makes it, or
the reverse-DCF over-weighting) and does NOT flag well-grounded headline figures. Then a
final holistic review + finish the branch (PR to `main`).

# Scenario-Coherence Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic coherence gate on the alpha thesis's priced scenario table; when it fires, do one corrective re-synthesis kept only if strictly more coherent, else surface a warning banner.

**Architecture:** A pure `scenario_coherence(thesis, dossier)` function (sibling to `alpha_completeness`) runs inside `_build_thesis` and populates `AlphaThesis.coherence_issues`. `run()` gates a single corrective `resynthesize_coherent` pass on `_coherence_score`. Render adds a §2 warning banner for residual issues. Zero LLM cost when coherent; +1 call only when the gate fires.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-13-scenario-coherence-gate-design.md`

**File structure (what each task touches):**
- `saturn/models.py` — `CoherenceIssue` type + `AlphaThesis.coherence_issues` field (Task 1)
- `saturn/agents/synthesist.py` — `scenario_coherence`, `_coherence_score`, `resynthesize_coherent`, constants, `_build_thesis` wiring (Tasks 2–3)
- `saturn/workflows/equity_research.py` — keep-if-more-coherent loop + import (Task 4)
- `saturn/reports/markdown_report.py` — `_render_coherence_banner` + call in `_render_alpha` (Task 5)

---

### Task 1: `CoherenceIssue` model + `AlphaThesis.coherence_issues` field

**Files:**
- Modify: `saturn/models.py` (add `CoherenceIssue` just before `class AlphaThesis` at line ~167; add a field to `AlphaThesis` after `incompleteness` at line ~181)
- Test: `tests/test_models_alpha.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models_alpha.py`:

```python
def test_coherence_issue_and_default_empty():
    from saturn.models import CoherenceIssue, AlphaThesis, ExpectationAnchor, Provenance
    issue = CoherenceIssue(check="monotonicity", severity="high", detail="bull below base")
    assert issue.check == "monotonicity" and issue.severity == "high"
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert a.coherence_issues == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py::test_coherence_issue_and_default_empty -v`
Expected: FAIL with `ImportError: cannot import name 'CoherenceIssue'`

- [ ] **Step 3: Write minimal implementation**

In `saturn/models.py`, add immediately before `class AlphaThesis(BaseModel):`:

```python
class CoherenceIssue(BaseModel):
    """A deterministic scenario-table coherence problem (computed, never LLM-authored)."""
    check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon"]
    severity: Literal["high", "medium"]
    detail: str
```

In `class AlphaThesis`, add after the `incompleteness: list[str] = Field(default_factory=list)` line:

```python
    coherence_issues: list[CoherenceIssue] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py::test_coherence_issue_and_default_empty -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_alpha.py
git commit -m "feat(models): CoherenceIssue + AlphaThesis.coherence_issues field"
```

---

### Task 2: `scenario_coherence` detection + `_build_thesis` wiring

**Files:**
- Modify: `saturn/agents/synthesist.py` (add `import re`; module constants near line 11; `scenario_coherence` after `alpha_completeness` ~line 102; one line in `_build_thesis` after `thesis.incompleteness = ...` ~line 202)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py` (the file already imports `ScenarioLeg`, `AlphaThesis`, `ExpectationAnchor`, `Provenance`, `ConsensusSnapshot`, `CompanyDossier`, and defines `_dossier`):

```python
from saturn.agents.synthesist import scenario_coherence


def _priced_leg(name, price, ret, value=10.0, mult=15.0, basis="P/E"):
    return ScenarioLeg(name=name, period="FY2027", driver="d", metric="EPS",
                       metric_basis="adjusted", per_share_value=value, multiple=mult,
                       multiple_basis=basis, implied_price=price, implied_return_pct=ret)


def _coh_thesis(legs, rationale=""):
    return AlphaThesis(
        anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
        stance="below_consensus", rationale=rationale, confidence="low",
        scenarios=legs, provenance=Provenance(source="Saturn (synthesist)"))


def test_coherence_flags_non_monotonic_prices():
    # bull priced BELOW bear -> high monotonicity issue
    legs = [_priced_leg("bull", 100.0, -0.1), _priced_leg("base", 150.0, 0.0),
            _priced_leg("bear", 200.0, 0.2)]
    issues = scenario_coherence(_coh_thesis(legs), _dossier())
    assert [i.check for i in issues] == ["monotonicity"]
    assert issues[0].severity == "high"


def test_coherence_clean_monotonic_table_has_no_issue():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, 0.0),
            _priced_leg("bear", 100.0, -0.2)]
    assert scenario_coherence(_coh_thesis(legs), _dossier()) == []


def test_coherence_flags_prose_vs_computed():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="Our base case implies ~+2% vs the Street's +7%.")
    issues = scenario_coherence(t, _dossier())
    assert [i.check for i in issues] == ["prose_vs_computed"]
    assert issues[0].severity == "medium"


def test_coherence_prose_matching_computed_is_clean():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="Our base case implies -40% vs the Street's +7%.")
    assert scenario_coherence(t, _dossier()) == []


def test_coherence_unparseable_prose_no_false_positive():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="The base case is cautious given execution risk.")
    assert scenario_coherence(t, _dossier()) == []


def test_coherence_flags_multiple_horizon():
    # consensus forward P/E 38x on forward EPS 6.0; a P/E leg at 38x applied to EPS 3.6 (< 0.8*6.0)
    cons = ConsensusSnapshot(forward_pe=38.0, forward_eps=6.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    legs = [_priced_leg("bull", 200.0, 0.2, value=4.8, mult=42.0),
            _priced_leg("base", 136.8, -0.42, value=3.6, mult=38.0),
            _priced_leg("bear", 81.2, -0.66, value=2.9, mult=28.0)]
    issues = scenario_coherence(_coh_thesis(legs), _dossier(consensus=cons))
    assert [i.check for i in issues] == ["multiple_horizon"]


def test_coherence_multiple_horizon_skipped_without_consensus():
    legs = [_priced_leg("base", 136.8, -0.42, value=3.6, mult=38.0)]
    assert scenario_coherence(_coh_thesis(legs), _dossier()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k coherence -v`
Expected: FAIL with `ImportError: cannot import name 'scenario_coherence'`

- [ ] **Step 3: Write minimal implementation**

In `saturn/agents/synthesist.py`, add `import re` to the imports block (top of file). Add module constants after `_STANCE_BAND = 0.10` (~line 11):

```python
_COHERENCE_MULTIPLE_TOL = 0.15   # a leg multiple within ±15% of consensus forward P/E is "the forward multiple"
_COHERENCE_EPS_FLOOR = 0.8       # ... applied to an EPS below 80% of consensus forward EPS is horizon-mismatched
_PROSE_RETURN_TOL = 0.15         # prose base return may differ from the computed base return by ≤15pp
_PROSE_RETURN_RE = re.compile(r"base case implies[^%]*?([+-]?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
```

Add the function after `alpha_completeness` (after its `return gaps`, ~line 102):

```python
def scenario_coherence(thesis: AlphaThesis, dossier: CompanyDossier) -> list["CoherenceIssue"]:
    """Deterministic coherence audit of the priced scenario table (sibling to alpha_completeness).
    Returns issues in a stable order: monotonicity, prose_vs_computed, multiple_horizon. Pure; any
    missing data skips that check rather than raising."""
    from saturn.models import CoherenceIssue
    issues: list[CoherenceIssue] = []
    legs = {s.name: s for s in thesis.scenarios}
    bull, base, bear = legs.get("bull"), legs.get("base"), legs.get("bear")

    # 1. Monotonicity — bull >= base >= bear in implied price.
    if bull and base and bear and all(x.implied_price is not None for x in (bull, base, bear)):
        if not (bull.implied_price >= base.implied_price >= bear.implied_price):
            issues.append(CoherenceIssue(
                check="monotonicity", severity="high",
                detail=(f"prices not monotonic: bull ${bull.implied_price:,.2f} / "
                        f"base ${base.implied_price:,.2f} / bear ${bear.implied_price:,.2f}")))

    # 2. Prose-vs-computed — the narrated base return must match the computed base return.
    if base is not None and base.implied_return_pct is not None:
        text = thesis.rationale or thesis.variant or ""
        m = _PROSE_RETURN_RE.search(text)
        if m:
            parsed = float(m.group(1)) / 100.0
            if abs(parsed - base.implied_return_pct) > _PROSE_RETURN_TOL:
                issues.append(CoherenceIssue(
                    check="prose_vs_computed", severity="medium",
                    detail=(f"rationale says base {parsed:+.0%} but the table computes "
                            f"{base.implied_return_pct:+.0%}")))

    # 3. Multiple-horizon — a forward (FY+1) P/E applied to a materially lower near-term EPS.
    cons = dossier.consensus
    if cons is not None and cons.forward_pe is not None and cons.forward_eps is not None:
        for s in thesis.scenarios:
            if (s.multiple_basis == "P/E"
                    and abs(s.multiple - cons.forward_pe) <= _COHERENCE_MULTIPLE_TOL * cons.forward_pe
                    and s.per_share_value < _COHERENCE_EPS_FLOOR * cons.forward_eps):
                issues.append(CoherenceIssue(
                    check="multiple_horizon", severity="medium",
                    detail=(f"{s.name} applies forward P/E {s.multiple:g}x to EPS "
                            f"${s.per_share_value:g} (< {_COHERENCE_EPS_FLOOR:g}× consensus forward "
                            f"EPS ${cons.forward_eps:g})")))
                break   # one horizon issue per table is enough
    return issues
```

In `_build_thesis`, after the line `thesis.incompleteness = alpha_completeness(thesis)`:

```python
    thesis.coherence_issues = scenario_coherence(thesis, dossier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k coherence -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): scenario_coherence detection + wire into _build_thesis"
```

---

### Task 3: `_coherence_score`

**Files:**
- Modify: `saturn/agents/synthesist.py` (add `_coherence_score` after `synthesize`, end of file)
- Test: `tests/agents/test_synthesist.py`

`resynthesize_coherent` is added in Task 4, driven by that task's integration test (it needs
`analysis`/`debate` objects that are simplest to produce through `run()`). Task 3 is the pure
scoring helper only.

- [ ] **Step 1: Write the failing test**

Add to `tests/agents/test_synthesist.py` (add `CoherenceIssue` to the `saturn.models` import at the top; `AlphaThesis`, `ExpectationAnchor`, `Provenance` are already imported):

```python
from saturn.agents.synthesist import _coherence_score


def test_coherence_score_weights():
    from saturn.models import CoherenceIssue
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"),
                    coherence_issues=[CoherenceIssue(check="monotonicity", severity="high", detail="x"),
                                      CoherenceIssue(check="prose_vs_computed", severity="medium", detail="y")])
    assert _coherence_score(a) == 3


def test_coherence_score_zero_when_clean():
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert _coherence_score(a) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k coherence_score -v`
Expected: FAIL with `ImportError: cannot import name '_coherence_score'`

- [ ] **Step 3: Write minimal implementation**

Append to `saturn/agents/synthesist.py` (after `synthesize`):

```python
def _coherence_score(alpha: AlphaThesis) -> int:
    """Severity-weighted coherence penalty (high=2, medium=1). Lower is better; 0 is coherent."""
    return sum(2 if i.severity == "high" else 1 for i in alpha.coherence_issues)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k coherence_score -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): _coherence_score helper"
```

---

### Task 4: `resynthesize_coherent` + keep-if-more-coherent loop in `run()`

**Files:**
- Modify: `saturn/agents/synthesist.py` (add `resynthesize_coherent` after `_coherence_score`)
- Modify: `saturn/workflows/equity_research.py` (import line ~17; insert block between `alpha = synthesize(...)` line 391 and `review = critique(...)` line 392)
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_equity_research_workflow.py` (mirrors the existing `_AlphaRepairLLM` pattern; `run`, `_mock_dossier`, `json`, `_ANALYSIS_KEYS` are already imported/defined there). Both tests set `d.consensus = None` so only the (consensus-independent) monotonicity check is in play — keeping the assertions deterministic regardless of the mock's consensus values:

```python
def _incoherent_scenarios():
    # non-monotonic: bull price 100 < bear price 200
    return [{"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 20.0, "multiple_basis": "P/E"}]


def _coherent_scenarios():
    return [{"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 20.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"}]


class _CoherenceRunLLM:
    """synth (incoherent) -> coherence gate -> re-synth (coherent iff improve) -> clean critic."""
    def __init__(self, improve=True):
        self.improve = improve
    def _alpha(self, scenarios):
        return json.dumps({"stance": "unclear", "variant": "v", "rationale": "r", "confidence": "low",
                           "key_variable": "k", "falsifier": "f", "horizon": "12m", "scenarios": scenarios})
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            if "coherence checks" in prompt:   # the corrective re-synthesis
                return self._alpha(_coherent_scenarios() if self.improve else _incoherent_scenarios())
            return self._alpha(_incoherent_scenarios())
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 1, "summary": "ok", "findings": []})
        return "{}"


def _coherence_dossier():
    d = _mock_dossier("MU")
    d.consensus = None   # isolate the monotonicity check (no consensus → multiple_horizon skipped)
    return d


def test_run_coherence_gate_replaces_when_improved():
    r = run(_coherence_dossier(), _CoherenceRunLLM(improve=True), model_used="m", mock=False)
    assert r.alpha_thesis is not None and r.alpha_thesis.coherence_issues == []


def test_run_coherence_gate_keeps_original_when_not_improved():
    r = run(_coherence_dossier(), _CoherenceRunLLM(improve=False), model_used="m", mock=False)
    assert any(i.check == "monotonicity" for i in r.alpha_thesis.coherence_issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k coherence_gate -v`
Expected: FAIL — `test_run_coherence_gate_replaces_when_improved` fails because the gate does not run yet (original incoherent thesis kept, `coherence_issues` non-empty). (`resynthesize_coherent` also does not exist yet → `ImportError` once the `run()` import is added; write the implementation in Step 3.)

- [ ] **Step 3: Write minimal implementation**

First, append `resynthesize_coherent` to `saturn/agents/synthesist.py` (after `_coherence_score`):

```python
def resynthesize_coherent(analysis, debate, dossier: CompanyDossier, llm, issues,
                          *, model: str | None = None) -> AlphaThesis | None:
    """One corrective synthesize pass: re-ask for a fully self-consistent thesis given the specific
    coherence problems. Reuses the synthesize machinery so prose AND scenarios regenerate together.
    Soft-fails to None; never breaks the report."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        anchor = _resolve_anchor(dossier)
        base_prompt = _synthesize_prompt(analysis, debate, anchor, _company_context(dossier))
        problems = "; ".join(f"[{i.check}] {i.detail}" for i in issues)
        corrective = (
            "\n\nYour previous scenario table failed these coherence checks: " + problems + ". "
            "Regenerate the FULL thesis so that: bull >= base >= bear in implied price; any P/E "
            "multiple matches the horizon of its EPS (do NOT apply a next-fiscal-year multiple to a "
            "near-term EPS); and the base-case return you describe in the rationale matches the base "
            "scenario you output. Do NOT output prices."
        )
        strict = ("\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single, "
                  "strictly valid JSON object.")
        for attempt in range(2):
            raw = llm.complete(SYNTHESIZE_SYSTEM,
                               base_prompt + corrective + ("" if attempt == 0 else strict),
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                return _build_thesis(json.loads(_extract_json(raw)), anchor, dossier)
            except Exception:  # noqa: BLE001 - malformed JSON; retry once then give up
                continue
        logger.warning("scenario re-synthesis unparseable for %s", getattr(dossier, "ticker", "?"))
        return None
    except Exception as exc:  # noqa: BLE001 - best-effort; never breaks the report
        logger.warning("scenario re-synthesis unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
```

Then, in `saturn/workflows/equity_research.py`, change the synthesist import (line ~17):

```python
from saturn.agents.synthesist import (
    _coherence_score, apply_alpha_corrections, resynthesize_coherent, synthesize,
)
```

Insert between `alpha = synthesize(...)` and `review = critique(...)`:

```python
    # Scenario-coherence gate: if the priced scenario table is internally incoherent (non-monotonic
    # prices, a rationale base return that contradicts the table, or a forward multiple applied to a
    # near-term EPS), do ONE corrective re-synthesis and keep it only if strictly more coherent.
    # Soft-fail keeps the original. Runs before critique so the Critic audits the coherent thesis.
    if alpha is not None and alpha.coherence_issues:
        r_alpha = resynthesize_coherent(analysis, deb, company, llm, alpha.coherence_issues, model=call_model)
        if r_alpha is not None and _coherence_score(r_alpha) < _coherence_score(alpha):
            alpha = r_alpha
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k coherence_gate -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): resynthesize_coherent + keep-if-more-coherent gate in run()"
```

---

### Task 5: `_render_coherence_banner` + call in `_render_alpha`

**Files:**
- Modify: `saturn/reports/markdown_report.py` (add `_render_coherence_banner` near `_render_high_severity_banner` ~line 176; call it inside `_render_alpha` just before the scenario table at ~line 161)
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_markdown_report.py` (it already constructs alpha theses for §2 tests; reuse its helpers or build inline):

```python
def test_render_coherence_banner_present_and_absent():
    from saturn.reports.markdown_report import _render_alpha
    from saturn.models import (AlphaThesis, CoherenceIssue, ExpectationAnchor, Provenance, ScenarioLeg)
    def _leg(n, p): return ScenarioLeg(name=n, period="FY2027", driver="d", metric="EPS",
        metric_basis="adjusted", per_share_value=10.0, multiple=15.0, multiple_basis="P/E", implied_price=p)
    base = dict(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                stance="below_consensus", variant="v", rationale="r", confidence="low",
                key_variable="k", falsifier="f", horizon="12m",
                scenarios=[_leg("bull", 100.0), _leg("base", 150.0), _leg("bear", 200.0)],
                provenance=Provenance(source="Saturn (synthesist)"))
    with_issue = AlphaThesis(coherence_issues=[CoherenceIssue(
        check="monotonicity", severity="high", detail="prices not monotonic")], **base)
    md = "\n".join(_render_alpha(with_issue))
    assert "Scenario coherence warning" in md and "prices not monotonic" in md
    # banner appears before the scenario table
    assert md.index("Scenario coherence warning") < md.index("| Scenario |")

    clean = AlphaThesis(**base)
    assert "Scenario coherence warning" not in "\n".join(_render_alpha(clean))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py::test_render_coherence_banner_present_and_absent -v`
Expected: FAIL with `AssertionError` (no banner text rendered)

- [ ] **Step 3: Write minimal implementation**

In `saturn/reports/markdown_report.py`, add near `_render_high_severity_banner`:

```python
def _render_coherence_banner(thesis) -> list[str]:
    """A prominent warning block for residual scenario-coherence issues, so a self-contradictory
    scenario table is never presented as authoritative. Empty when the table is coherent."""
    issues = getattr(thesis, "coherence_issues", None)
    if not issues:
        return []
    out = ["> ⚠️ **Scenario coherence warning(s)** — treat the scenario returns as provisional:"]
    for i in issues:
        out.append(f">   • [{i.check}] {i.detail}")
    out.append("")
    return out
```

In `_render_alpha`, insert the call immediately before `if thesis.scenarios:` (~line 161):

```python
    out += _render_coherence_banner(thesis)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py::test_render_coherence_banner_present_and_absent -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests + the new ones; target ~380 passing)

- [ ] **Step 6: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): §2 scenario-coherence warning banner"
```

---

## Final verification (after all tasks)

- [ ] Run the full suite: `.venv/Scripts/python.exe -m pytest -q` — all green.
- [ ] Offline smoke (no LLM): build a dossier and confirm a hand-built incoherent thesis yields issues and a coherent one does not, via `scenario_coherence`.
- [ ] Live (optional, costs 1 API call baseline + up to 1 for the gate): `.venv/Scripts/python.exe -m saturn.cli research MRVL` and confirm the §2 scenario table is either coherent (no banner) or carries the coherence banner, and that the base-return prose no longer contradicts the table.

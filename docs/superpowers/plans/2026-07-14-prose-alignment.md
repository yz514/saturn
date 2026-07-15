# Deterministic Prose-Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `prose_vs_computed` in code (correct the stated base return to the computed value) and fire LLM re-synthesis only for `monotonicity`; accept `bull_below_spot`/`multiple_horizon` as an honest banner.

**Architecture:** A pure `align_prose_base_return(thesis)` corrector runs in `_build_thesis` (and at the end-of-`run()` recompute) before coherence is computed. The multi-pass gate condition narrows from "any coherence issue" to "a monotonicity issue remains".

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-14-prose-alignment-design.md`

**File structure:**
- `saturn/agents/synthesist.py` — `align_prose_base_return` + `_build_thesis` call (Task 1)
- `saturn/workflows/equity_research.py` — monotonicity-only trigger + end-of-run align + import (Task 2)

---

### Task 1: `align_prose_base_return` + `_build_thesis` call

**Files:**
- Modify: `saturn/agents/synthesist.py` (new function near `scenario_coherence`; one call in `_build_thesis` before the `thesis.incompleteness = ...` line ~269)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py` (it already imports `AlphaThesis`, `ExpectationAnchor`, `Provenance`, `ScenarioLeg`, and defines `_dossier`, `_priced_leg`, and imports `scenario_coherence`):

```python
from saturn.agents.synthesist import align_prose_base_return


def _align_thesis(rationale="", variant="", base_ret=-0.47):
    legs = [_priced_leg("base", 100.0, base_ret)]
    return AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                       stance="below_consensus", variant=variant, rationale=rationale, confidence="low",
                       scenarios=legs, provenance=Provenance(source="Saturn (synthesist)"))


def test_align_corrects_divergent_rationale():
    t = _align_thesis(rationale="Our base case implies ~+6% vs the Street's +14%.", base_ret=-0.47)
    align_prose_base_return(t)
    assert "-47%" in t.rationale and "+6%" not in t.rationale
    # the corrected prose no longer trips the coherence check
    assert not any(i.check == "prose_vs_computed" for i in scenario_coherence(t, _dossier()))


def test_align_noop_within_tolerance():
    t = _align_thesis(rationale="Our base case implies -40% vs the Street's +14%.", base_ret=-0.47)  # 7pp
    align_prose_base_return(t)
    assert "-40%" in t.rationale


def test_align_noop_no_cue():
    t = _align_thesis(rationale="The base case is cautious and execution-dependent.", base_ret=-0.47)
    align_prose_base_return(t)
    assert t.rationale == "The base case is cautious and execution-dependent."


def test_align_noop_no_base_leg():
    t = AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                    stance="below_consensus", rationale="Our base case implies +6% vs Street.",
                    scenarios=[_priced_leg("bull", 100.0, 0.1)],
                    provenance=Provenance(source="Saturn (synthesist)"))
    align_prose_base_return(t)          # no base leg -> no-op, no crash
    assert "+6%" in t.rationale


def test_align_corrects_variant():
    t = _align_thesis(variant="Base case implies +6% as consensus overreaches.", rationale="", base_ret=-0.47)
    align_prose_base_return(t)
    assert "-47%" in t.variant


def test_align_positive_computed():
    t = _align_thesis(rationale="Our base case implies -5% vs the Street.", base_ret=0.12)
    align_prose_base_return(t)
    assert "+12%" in t.rationale
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k align -v`
Expected: FAIL — `ImportError: cannot import name 'align_prose_base_return'`.

- [ ] **Step 3: Implement**

In `saturn/agents/synthesist.py`, add this function immediately after `scenario_coherence` (before `apply_alpha_corrections` / wherever fits near the coherence helpers):

```python
def align_prose_base_return(thesis: AlphaThesis) -> None:
    """Correct a stated base-case return in the prose to match the computed base scenario, in place.
    Mirrors deterministic stance derivation: the LLM owns the argument, code owns the number. Uses the
    same cue/tolerance as the prose_vs_computed check, so afterwards that check cannot fire on a
    parseable, divergent figure. No-ops when there is no base leg / computed return, no cue, or the
    figure is already within tolerance."""
    base = next((s for s in thesis.scenarios if s.name == "base"), None)
    if base is None or base.implied_return_pct is None:
        return
    computed = f"{base.implied_return_pct * 100:+.0f}"          # e.g. "-47" or "+12"

    def _fix(text: str) -> str:
        m = _PROSE_RETURN_RE.search(text)
        if m and abs(float(m.group(1)) / 100.0 - base.implied_return_pct) > _PROSE_RETURN_TOL:
            return _PROSE_RETURN_RE.sub(lambda mm: mm.group(0).replace(mm.group(1), computed, 1),
                                        text, count=1)
        return text

    thesis.variant = _fix(thesis.variant)
    thesis.rationale = _fix(thesis.rationale)
```

In `_build_thesis`, insert the call immediately before the existing `thesis.incompleteness = alpha_completeness(thesis)` line:

```python
    align_prose_base_return(thesis)
    thesis.incompleteness = alpha_completeness(thesis)
    thesis.coherence_issues = scenario_coherence(thesis, dossier)
    return thesis
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k align -v` → PASS (6).
Then: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` → whole file green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): deterministic prose base-return alignment"
```

---

### Task 2: monotonicity-only repair trigger + end-of-run align

**Files:**
- Modify: `saturn/workflows/equity_research.py` (import; the gate `while` condition ~line 401; the end-of-run recompute ~line 445–446)
- Test: `tests/test_equity_research_workflow.py` (revise 2 slice-2 tests, add 2 new)

- [ ] **Step 1: Write / revise the tests**

(1a) **Revise** `test_run_multipass_two_passes_to_coherent` — make it monotonicity-driven (monotonicity persists through pass 1, fixed in pass 2). Replace the existing function body with:

```python
def test_run_multipass_two_passes_to_coherent():
    # monotonicity-only trigger: mono persists through pass1, fixed in pass2 (stance 'unclear').
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # 150/180/220: non-monotonic(2) + bull -25%(2) = 4
    pass1 = _mp_legs((10, 21), (10, 24), (10, 26))     # 210/240/260: non-monotonic(2), bull +5% above = 2
    pass2 = _mp_legs((10, 24), (10, 21), (10, 18))     # 240/210/180: monotonic, bull +20% = 0
    llm = _MultiPassLLM("unclear", initial, [pass1, pass2])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert r.alpha_thesis.coherence_issues == []
    assert llm.resynth == 2
```

(1b) **Revise** `test_run_multipass_caps_at_two_even_if_still_improving` — the cap only binds if monotonicity persists across 2 improving passes, which needs a 3rd gradable issue (`multiple_horizon`), so this variant uses a dossier WITH consensus. Add a helper and replace the test body:

```python
def _mp_cons_dossier():
    # like _mp_dossier but WITH a consensus (forward_pe/forward_eps for multiple_horizon; target_mean
    # None so stance stays LLM-declared) — lets a monotonicity issue persist across two improving passes.
    from saturn.models import ConsensusSnapshot, Provenance
    d = _mock_dossier("MU")
    d.consensus = ConsensusSnapshot(forward_pe=20.0, forward_eps=10.0,
                                    provenance=Provenance(source="yfinance (estimate)"))
    d.quote.price = 200.0
    return d


def test_run_multipass_caps_at_two_even_if_still_improving():
    # stance 'below_consensus'; consensus fwd_pe=20/fwd_eps=10. Scores 4->3->2, mono present throughout,
    # so the cap (not "monotonicity resolved") is the binding stop; a 3rd pass (->0) is never reached.
    initial = _mp_legs((5, 20), (10, 18), (10, 22))    # 100/180/220: non-mono(2)+bull -50% med(1)+horizon(bull 20x,EPS5<8)(1)=4
    pass1 = _mp_legs((5, 30), (5, 40), (5, 44))        # 150/200/220: non-mono(2)+bull -25% med(1), no horizon (mults 30/40/44) = 3
    pass2 = _mp_legs((5, 50), (5, 48), (5, 52))        # 250/240/260: non-mono(2), bull +25% above, no horizon = 2
    pass3 = _mp_legs((10, 24), (10, 21), (10, 18))     # would be 0, never reached
    llm = _MultiPassLLM("below_consensus", initial, [pass1, pass2, pass3])
    r = run(_mp_cons_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 2                             # capped (not "monotonicity resolved")
    assert any(i.check == "monotonicity" for i in r.alpha_thesis.coherence_issues)
```

(1c) **Add** a test for the KEY new behavior — no monotonicity → 0 re-synthesis:

```python
def test_run_multipass_no_resynth_without_monotonicity():
    # monotonic table whose only issue is bull_below_spot -> the mono-only trigger does NOT fire.
    table = _mp_legs((10, 19), (10, 17), (10, 15))     # 190/170/150 monotonic, bull -5% below spot (high)
    llm = _MultiPassLLM("unclear", table, [table])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 0
    assert any(i.check == "bull_below_spot" for i in r.alpha_thesis.coherence_issues)
```

(`test_run_multipass_stops_when_no_improvement` and `test_run_multipass_already_coherent_no_resynth`
are unaffected — the former's initial table is non-monotonic so the trigger still fires; the latter is
coherent so it never fires. Leave them as-is.)

- [ ] **Step 2: Run to verify the new/revised tests fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k multipass -v`
Expected: FAIL — under the current "any issue" trigger, `test_run_multipass_no_resynth_without_monotonicity` sees `resynth == 1` (not 0) because `bull_below_spot` still triggers; the revised `caps` test also mismatches.

- [ ] **Step 3: Implement**

(3a) In `saturn/workflows/equity_research.py`, add `align_prose_base_return` to the synthesist import (currently `from saturn.agents.synthesist import (_coherence_score, apply_alpha_corrections, resynthesize_coherent, scenario_coherence, synthesize,)`):

```python
from saturn.agents.synthesist import (
    _coherence_score, align_prose_base_return, apply_alpha_corrections, resynthesize_coherent,
    scenario_coherence, synthesize,
)
```

(3b) Change the gate loop condition. Replace the existing line
`    while alpha is not None and alpha.coherence_issues and attempts < _MAX_COHERENCE_REPAIRS:`
with:

```python
    while (alpha is not None
           and any(i.check == "monotonicity" for i in alpha.coherence_issues)
           and attempts < _MAX_COHERENCE_REPAIRS):
```

Also update the gate comment's first sentence to reflect the narrowed trigger — change it to read:

```python
    # Scenario-coherence gate: re-synthesize (up to _MAX_COHERENCE_REPAIRS times) ONLY for a
    # monotonicity issue (a gross ordering error the LLM can fix), keeping each pass only if
    # _coherence_score strictly improves; stop on no improvement. prose_vs_computed is fixed
    # deterministically in _build_thesis; bull_below_spot / multiple_horizon are accepted as an
    # honest banner. Runs before critique. Soft-fail (None) keeps the current thesis.
```

(3c) At the end-of-run recompute, align before recomputing. Replace:

```python
    if alpha is not None:
        alpha.coherence_issues = scenario_coherence(alpha, company)
```

with:

```python
    if alpha is not None:
        align_prose_base_return(alpha)          # re-correct any base return the prose-repair rewrote
        alpha.coherence_issues = scenario_coherence(alpha, company)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k multipass -v` → PASS (5).
Then the whole file: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q` → all green (expect ~402).

- [ ] **Step 6: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): monotonicity-only repair trigger + end-of-run prose align"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest -q`.
- [ ] Offline sanity (no LLM): build a thesis whose base leg return is −0.47 and whose rationale says "base case implies +6%"; `align_prose_base_return` rewrites it to "-47%", and `scenario_coherence` then returns no `prose_vs_computed`.
- [ ] Live (optional; MRVL runs ~10-13 min — build the dossier once and pass it to `run()` to dodge yfinance flakiness): regenerate MRVL — confirm the §2 prose now states a base return consistent with the table (≈−47%), `prose_vs_computed` is gone, the banner shows only `[multiple_horizon, bull_below_spot]`, and **0** re-synthesis passes ran. Regenerate AVGO — confirm still coherent, no banner.

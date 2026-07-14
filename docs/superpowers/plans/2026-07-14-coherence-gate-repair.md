# Coherence Gate Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `bull_below_spot` coherence check and make the corrective repair stronger — a horizon-matching prompt rule + arithmetic hint and a bounded 2-pass re-synthesis.

**Architecture:** Extends the merged scenario-coherence gate. `scenario_coherence` gains a 4th check; `SYNTHESIZE_SYSTEM` and `resynthesize_coherent`'s corrective prompt gain horizon guidance; `run()`'s single-shot gate becomes a bounded loop keeping each pass only if `_coherence_score` strictly improves.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-13-coherence-gate-repair-design.md`

**File structure:**
- `saturn/models.py` — add `"bull_below_spot"` to the `CoherenceIssue.check` Literal (Task 1)
- `saturn/agents/synthesist.py` — `bull_below_spot` check (Task 2); `SYNTHESIZE_SYSTEM` rule + `resynthesize_coherent` hint (Task 3)
- `saturn/workflows/equity_research.py` — `_MAX_COHERENCE_REPAIRS` + bounded loop (Task 4)

---

### Task 1: add `bull_below_spot` to the `CoherenceIssue` literal

**Files:**
- Modify: `saturn/models.py` (the `check:` Literal on `CoherenceIssue`, ~line 169)
- Test: `tests/test_models_alpha.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models_alpha.py`:

```python
def test_coherence_issue_accepts_bull_below_spot():
    from saturn.models import CoherenceIssue
    issue = CoherenceIssue(check="bull_below_spot", severity="medium", detail="bull below spot")
    assert issue.check == "bull_below_spot"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py::test_coherence_issue_accepts_bull_below_spot -v`
Expected: FAIL — pydantic `ValidationError` (literal doesn't allow `bull_below_spot`).

- [ ] **Step 3: Implement**

In `saturn/models.py`, change the `CoherenceIssue.check` line to:

```python
    check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon", "bull_below_spot"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py::test_coherence_issue_accepts_bull_below_spot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py tests/test_models_alpha.py
git commit -m "feat(models): add bull_below_spot to CoherenceIssue.check literal"
```

---

### Task 2: `bull_below_spot` detection check

**Files:**
- Modify: `saturn/agents/synthesist.py` (`scenario_coherence`, immediately before `return issues`)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py` (the file already imports `AlphaThesis`, `ExpectationAnchor`, `Provenance`, `ScenarioLeg`, defines `_dossier`, `_priced_leg`, and imports `scenario_coherence`):

```python
def _bull_thesis(bull_ret, stance, bull_price=100.0):
    # prices monotonic (100>=90>=80) so ONLY the bull_below_spot check can fire; rationale empty so
    # prose_vs_computed is skipped; _dossier() has no consensus so multiple_horizon is skipped.
    legs = [_priced_leg("bull", bull_price, bull_ret),
            _priced_leg("base", 90.0, -0.3), _priced_leg("bear", 80.0, -0.5)]
    return AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                       stance=stance, rationale="", confidence="low", scenarios=legs,
                       provenance=Provenance(source="Saturn (synthesist)"))


def test_bull_below_spot_high_for_nonbearish_stances():
    for stance in ("above_consensus", "in_line_consensus", "unclear"):
        issues = scenario_coherence(_bull_thesis(-0.19, stance), _dossier())
        assert [i.check for i in issues] == ["bull_below_spot"]
        assert issues[0].severity == "high"


def test_bull_below_spot_medium_for_below_consensus():
    issues = scenario_coherence(_bull_thesis(-0.19, "below_consensus"), _dossier())
    assert [i.check for i in issues] == ["bull_below_spot"]
    assert issues[0].severity == "medium"


def test_bull_at_or_above_spot_no_issue():
    assert scenario_coherence(_bull_thesis(0.05, "in_line_consensus"), _dossier()) == []
    assert scenario_coherence(_bull_thesis(0.0, "in_line_consensus"), _dossier()) == []


def test_bull_none_return_no_issue():
    legs = [_priced_leg("bull", 100.0, None), _priced_leg("base", 90.0, -0.3),
            _priced_leg("bear", 80.0, -0.5)]
    t = AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                    stance="in_line_consensus", scenarios=legs,
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert scenario_coherence(t, _dossier()) == []


def test_bull_below_spot_orders_after_monotonicity():
    # bull priced BELOW base (non-monotonic) AND bull return < 0 -> two issues, stable order
    issues = scenario_coherence(_bull_thesis(-0.19, "unclear", bull_price=70.0), _dossier())
    assert [i.check for i in issues] == ["monotonicity", "bull_below_spot"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "bull_below_spot or bull_at_or_above or bull_none" -v`
Expected: FAIL (the check doesn't exist yet → no `bull_below_spot` issues produced).

- [ ] **Step 3: Implement**

In `saturn/agents/synthesist.py`, inside `scenario_coherence`, insert this block immediately before the final `return issues`:

```python
    # 4. Bull-below-spot — a "bull" scenario that loses money. Unambiguously wrong unless the stance
    # is itself bearish (below_consensus), where a below-spot bull can be a deliberate short.
    if bull is not None and bull.implied_return_pct is not None and bull.implied_return_pct < 0:
        sev = "medium" if thesis.stance == "below_consensus" else "high"
        issues.append(CoherenceIssue(
            check="bull_below_spot", severity=sev,
            detail=(f"bull scenario returns {bull.implied_return_pct:+.0%} (below spot) despite a "
                    f"{thesis.stance} stance")))
```

Also update the function's docstring stable-order line to include `bull_below_spot`:

```python
    Returns issues in a stable order: monotonicity, prose_vs_computed, multiple_horizon,
    bull_below_spot. Pure; any missing data skips that check rather than raising."""
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k coherence -v` (all coherence tests, old + new)
Expected: PASS. Then `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` — full file green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): bull_below_spot coherence check (severity by stance)"
```

---

### Task 3: horizon rule in `SYNTHESIZE_SYSTEM` + arithmetic hint in `resynthesize_coherent`

**Files:**
- Modify: `saturn/agents/synthesist.py` (`SYNTHESIZE_SYSTEM` final sentence; `resynthesize_coherent` `corrective` construction)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py`:

```python
def test_synthesize_system_has_horizon_rule():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    s = SYNTHESIZE_SYSTEM.lower()
    assert "same horizon" in s and "never apply a forward multiple" in s


class _CapLLM:
    def __init__(self): self.prompt = ""
    def complete(self, system, prompt, *, model=None, max_tokens=8192):
        self.prompt = prompt
        return ('{"stance":"unclear","variant":"v","rationale":"r","confidence":"low",'
                '"key_variable":"k","falsifier":"f","horizon":"12m","scenarios":[]}')


class _FakeSections:
    def model_dump(self): return {}


def test_resynthesize_corrective_includes_arithmetic_hint():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.agents.synthesist import resynthesize_coherent
    from saturn.models import CoherenceIssue
    d = _mock_dossier("MU")
    d.consensus.forward_pe = 38.0
    d.consensus.forward_eps = 6.18
    d.driver_model.saturn_eps = 3.24
    llm = _CapLLM()
    resynthesize_coherent(_FakeSections(), _FakeSections(), d, llm,
                          [CoherenceIssue(check="multiple_horizon", severity="medium", detail="x")],
                          model=None)
    assert "horizon error" in llm.prompt
    assert "38x" in llm.prompt and "$6.18" in llm.prompt and "$3.24" in llm.prompt


def test_resynthesize_corrective_hint_omitted_without_consensus():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.agents.synthesist import resynthesize_coherent
    from saturn.models import CoherenceIssue
    d = _mock_dossier("MU")
    d.consensus = None
    llm = _CapLLM()
    resynthesize_coherent(_FakeSections(), _FakeSections(), d, llm,
                          [CoherenceIssue(check="monotonicity", severity="high", detail="x")],
                          model=None)
    assert "horizon error" not in llm.prompt         # hint guarded off, no crash
    assert "coherence checks" in llm.prompt           # base corrective still present
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "horizon_rule or arithmetic_hint or hint_omitted" -v`
Expected: FAIL — `test_synthesize_system_has_horizon_rule` fails (text absent); `test_resynthesize_corrective_includes_arithmetic_hint` fails (no `"horizon error"` in prompt).

- [ ] **Step 3: Implement**

(3a) In `SYNTHESIZE_SYSTEM`, change the final sentence. Replace:

```python
    "under 35 words. Respond with ONLY a single valid JSON object, no prose, no code fences."
```

with:

```python
    "under 35 words. "
    "CRITICAL — horizon match: the multiple and the per-share value must be the SAME horizon. If you "
    "use a forward (next-fiscal-year) P/E, pair it with the forward EPS; NEVER apply a forward "
    "multiple to a trailing or near-term EPS — that mechanically underprices every scenario. Before "
    "finalizing, verify bull >= base >= bear in implied price and that the base-case return you "
    "describe matches the base scenario. "
    "Respond with ONLY a single valid JSON object, no prose, no code fences."
```

(3b) In `resynthesize_coherent`, replace the `corrective = (...)` assignment with a version that appends the guarded arithmetic hint. The new code (keep the existing `problems = ...` line just above it):

```python
        cons, dm = dossier.consensus, dossier.driver_model
        hint = ""
        if cons and cons.forward_pe and cons.forward_eps and dm and dm.saturn_eps:
            hint = (f" For reference, the stock trades at ~{cons.forward_pe:.0f}x its forward EPS "
                    f"${cons.forward_eps:.2f} (which equals spot). Applying ~{cons.forward_pe:.0f}x "
                    f"to a near-term EPS like ${dm.saturn_eps:.2f} yields a price far below spot — "
                    f"that is the horizon error. Either pair forward EPS with the forward multiple, "
                    f"or use a near-term multiple (~15-20x) with the near-term EPS.")
        corrective = (
            "\n\nYour previous scenario table failed these coherence checks: " + problems + ". "
            "Regenerate the FULL thesis so that: bull >= base >= bear in implied price; any P/E "
            "multiple matches the horizon of its EPS (do NOT apply a next-fiscal-year multiple to a "
            "near-term EPS); and the base-case return you describe in the rationale matches the base "
            "scenario you output. Do NOT output prices." + hint
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "horizon_rule or arithmetic_hint or hint_omitted" -v`
Expected: PASS. Then `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` — full file green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): horizon-match rule in SYNTHESIZE_SYSTEM + arithmetic repair hint"
```

---

### Task 4: bounded multi-pass repair in `run()`

**Files:**
- Modify: `saturn/workflows/equity_research.py` (add `_MAX_COHERENCE_REPAIRS` near line ~60 constants; replace the gate block at ~lines 394–400)
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_equity_research_workflow.py` (the file already imports `run`, `_mock_dossier`, `json`, defines `_ANALYSIS_KEYS`):

```python
def _mp_legs(bull, base, bear):
    # each arg is (per_share_value, multiple); price = value*multiple, return computed vs quote
    def leg(name, vm):
        return {"name": name, "period": "FY2027", "driver": "d", "metric": "EPS",
                "metric_basis": "adjusted", "per_share_value": vm[0], "multiple": vm[1],
                "multiple_basis": "P/E"}
    return [leg("bull", bull), leg("base", base), leg("bear", bear)]


class _MultiPassLLM:
    """Returns `initial` on the first synthesize; on each corrective re-synthesis (prompt contains
    'coherence checks') returns the next table from `resynth_tables` (clamped to the last)."""
    def __init__(self, stance, initial, resynth_tables):
        self.stance = stance
        self.initial = initial
        self.resynth_tables = resynth_tables
        self.resynth = 0
    def _alpha(self, legs):
        return json.dumps({"stance": self.stance, "variant": "v", "rationale": "r",
                           "confidence": "low", "key_variable": "k", "falsifier": "f",
                           "horizon": "12m", "scenarios": legs})
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "o" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            if "coherence checks" in prompt:
                t = self.resynth_tables[min(self.resynth, len(self.resynth_tables) - 1)]
                self.resynth += 1
                return self._alpha(t)
            return self._alpha(self.initial)
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 1, "summary": "ok", "findings": []})
        return "{}"


def _mp_dossier():
    d = _mock_dossier("MU")
    d.consensus = None       # no target -> stance stays LLM-declared; multiple_horizon skipped
    d.quote.price = 200.0    # controls implied returns: price 150 -> -25%, 190 -> -5%, 240 -> +20%
    return d


def test_run_multipass_two_passes_to_coherent():
    # stance 'unclear' -> bull_below_spot is HIGH(2). Scores: initial 4 -> pass1 2 -> pass2 0.
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # prices 150/180/220: non-monotonic(2) + bull -25%(2) = 4
    pass1 = _mp_legs((10, 19), (10, 17), (10, 15))     # prices 190/170/150: monotonic, bull -5%(2) = 2
    pass2 = _mp_legs((10, 24), (10, 21), (10, 18))     # prices 240/210/180: monotonic, bull +20% = 0
    llm = _MultiPassLLM("unclear", initial, [pass1, pass2])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert r.alpha_thesis.coherence_issues == []
    assert llm.resynth == 2


def test_run_multipass_stops_when_no_improvement():
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # score 4
    llm = _MultiPassLLM("unclear", initial, [initial])  # re-synth returns the same table -> no improvement
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 1                              # stopped after one non-improving pass
    assert r.alpha_thesis.coherence_issues != []


def test_run_multipass_already_coherent_no_resynth():
    coherent = _mp_legs((10, 24), (10, 21), (10, 18))   # monotonic, bull +20% -> score 0
    llm = _MultiPassLLM("unclear", coherent, [coherent])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 0
    assert r.alpha_thesis.coherence_issues == []


def test_run_multipass_caps_at_two_even_if_still_improving():
    # stance 'below_consensus' -> bull_below_spot is MEDIUM(1). Scores strictly improve 3->2->1 but a
    # 3rd pass (would be 0) is blocked by the cap; the loop stops at 2 with residual issues.
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # 150/180/220: non-monotonic(2) + bull -25% med(1) = 3
    pass1 = _mp_legs((10, 21), (10, 24), (10, 26))     # 210/240/260: non-monotonic(2), bull +5% = 2
    pass2 = _mp_legs((10, 19), (10, 17), (10, 15))     # 190/170/150: monotonic, bull -5% med(1) = 1
    pass3 = _mp_legs((10, 24), (10, 21), (10, 18))     # would be 0, but never reached
    llm = _MultiPassLLM("below_consensus", initial, [pass1, pass2, pass3])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 2                             # capped
    assert r.alpha_thesis.coherence_issues != []        # residual issue remains
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k multipass -v`
Expected: FAIL — with the current single-shot gate, `test_run_multipass_two_passes_to_coherent` keeps only 1 pass so `coherence_issues` is non-empty and `resynth == 1` (not 2); the caps test also mismatches.

- [ ] **Step 3: Implement**

(3a) In `saturn/workflows/equity_research.py`, add a constant near the other `_...` constants (after `_MAX_OUTPUT_TOKENS = 8192`, ~line 60):

```python
_MAX_COHERENCE_REPAIRS = 2
```

(3b) Replace the existing gate block (the comment + `if alpha is not None and alpha.coherence_issues:` … `alpha = r_alpha`) with:

```python
    # Scenario-coherence gate: when the priced scenario table is incoherent, re-synthesize (up to
    # _MAX_COHERENCE_REPAIRS times) and keep each pass only if _coherence_score strictly improves;
    # stop as soon as a pass fails to improve (bounds cost on tables that can't be repaired, e.g. a
    # legitimately-bearish thesis whose bull is intrinsically below spot). Runs before critique so
    # the Critic audits the most-coherent thesis. Soft-fail (None) keeps the current thesis.
    attempts = 0
    while alpha is not None and alpha.coherence_issues and attempts < _MAX_COHERENCE_REPAIRS:
        attempts += 1
        r_alpha = resynthesize_coherent(analysis, deb, company, llm, alpha.coherence_issues, model=call_model)
        if r_alpha is None or _coherence_score(r_alpha) >= _coherence_score(alpha):
            break
        alpha = r_alpha
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -k multipass -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all green (~394+ tests).

- [ ] **Step 6: Commit**

```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): bounded multi-pass coherence repair (_MAX_COHERENCE_REPAIRS=2)"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest -q`.
- [ ] Offline sanity (no LLM): `scenario_coherence` on a hand-built `bull` leg with `implied_return_pct=-0.19` yields a `bull_below_spot` issue whose severity is `high` for a non-bearish stance and `medium` for `below_consensus`.
- [ ] Live (optional, costs LLM calls; note MRVL runs ~10-13 min — build the dossier once and pass it to `run()` if the CLI's yfinance fetch is flaky): regenerate MRVL and AVGO. Confirm MRVL's repair now clears more issues than before or still banners (possibly including a `medium bull_below_spot`), and AVGO stays coherent (no regression from the `SYNTHESIZE_SYSTEM` change).

# Prose Scenario-Math Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the scenario table the single source of truth — verify (and correct) the LLM's own scenario math in prose, and flag prose that cites a scenario the table doesn't contain.

**Architecture:** One pure parser (`_prose_math_claims`) feeds two new deterministic checks in `scenario_coherence` and one corrector (`align_prose_scenario_math`) that sits beside `align_prose_base_return` at its two existing call sites. Sharing the parser keeps check and corrector from drifting apart.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-prose-scenario-math-design.md`

> **⚠️ MERGE ORDER — read first.** This branch is cut from `main`, which does **not** yet contain PR #39 (NTM blending). P1 is functionally independent of #39, but both edit `saturn/agents/synthesist.py` in the same regions (the `_PROSE_*` constants block and `scenario_coherence`). **Merge #39 first, then rebase this branch onto `main` before implementing.** If you implement first, expect a conflict in `synthesist.py` — resolve by keeping BOTH (#39's `_PROSE_RETURN_TOL = 0.02` and this slice's new `_PROSE_MATH_*`/`_PROSE_LEG_TOL` constants).

**File structure:**
- `saturn/models.py` — two new `CoherenceIssue.check` literals (T1)
- `saturn/agents/synthesist.py` — constants + regexes + `_prose_math_claims` (T1); `prose_arithmetic` check (T2); `align_prose_scenario_math` + call sites (T3); `prose_scenario_not_in_table` check (T4)

---

### Task 1: literals, constants, and the `_prose_math_claims` parser

**Files:**
- Modify: `saturn/models.py` (`CoherenceIssue.check` Literal, ~line 169)
- Modify: `saturn/agents/synthesist.py` (constants beside `_PROSE_RETURN_RE` ~line 16; parser above `scenario_coherence`)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py`:

```python
from saturn.agents.synthesist import _prose_math_claims


def test_prose_math_claims_parses_pair_and_price():
    text = "base FY2027E: 20.5 EPS × 22.5 P/E, yielding an implied price near $358."
    claims = _prose_math_claims(text)
    assert len(claims) == 1
    a, b, price, start, end = claims[0]
    assert a == 20.5 and b == 22.5 and price == 358.0
    assert text[start:end] == "358"          # span covers the NUMBER only, not the "$"


def test_prose_math_claims_accepts_ascii_x_and_commas():
    claims = _prose_math_claims("20.5 EPS x 22.5 P/E gives $1,461.25 per share")
    assert len(claims) == 1 and claims[0][:3] == (20.5, 22.5, 1461.25)


def test_prose_math_claims_price_none_when_too_far():
    text = "20.5 EPS × 22.5 P/E" + " filler" * 40 + " $358"      # >120 chars away
    claims = _prose_math_claims(text)
    assert len(claims) == 1 and claims[0][2] is None


def test_prose_math_claims_empty_without_a_pair():
    assert _prose_math_claims("The base case is cautious; the stock trades at $395.63.") == []


def test_coherence_issue_accepts_the_two_new_checks():
    from saturn.models import CoherenceIssue
    for name in ("prose_arithmetic", "prose_scenario_not_in_table"):
        assert CoherenceIssue(check=name, severity="medium", detail="d").check == name
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "prose_math_claims or two_new_checks" -v`
Expected: FAIL — `ImportError: cannot import name '_prose_math_claims'`.

- [ ] **Step 3: Implement**

(3a) In `saturn/models.py`, extend the `CoherenceIssue.check` Literal to:
```python
    check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon", "bull_below_spot",
                   "prose_arithmetic", "prose_scenario_not_in_table"]
```

(3b) In `saturn/agents/synthesist.py`, add beside the existing `_PROSE_RETURN_RE` line:
```python
_PROSE_MATH_TOL = 0.02        # a stated A*B may differ from the true product only by rounding
_PROSE_LEG_TOL = 0.01         # a cited (value, multiple) must match a table leg this closely.
                              # NOT 2%: the real smuggled pair 18.86x19 sits 1.95% from a bear leg of
                              # 18.5x19, so a 2% tolerance would match it and defeat the check.
_PROSE_MATH_LOOKAHEAD = 120   # chars after a cited pair in which to look for its claimed price
_PROSE_PAIR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:EPS|FCF/share|sales/share)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:P/E|P/FCF|P/S|x)\b",
    re.IGNORECASE)
_PROSE_PRICE_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
```

(3c) Add this pure parser immediately ABOVE `def scenario_coherence(`:
```python
def _prose_math_claims(text: str) -> list[tuple[float, float, float | None, int, int]]:
    """Every 'A EPS × B P/E' pair the prose asserts, each with the price it claims (when one follows
    within _PROSE_MATH_LOOKAHEAD chars) and that price NUMBER's span. Shared by the prose_arithmetic
    check and align_prose_scenario_math so they cannot drift apart. Pure; [] when there is no pair.
    Items are (value, multiple, claimed_price | None, price_start, price_end)."""
    out: list[tuple[float, float, float | None, int, int]] = []
    for m in _PROSE_PAIR_RE.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        window = text[m.end(): m.end() + _PROSE_MATH_LOOKAHEAD]
        pm = _PROSE_PRICE_RE.search(window)
        if pm:
            out.append((a, b, float(pm.group(1).replace(",", "")),
                        m.end() + pm.start(1), m.end() + pm.end(1)))
        else:
            out.append((a, b, None, -1, -1))
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "prose_math_claims or two_new_checks" -v` → PASS (5).
Then: `.venv/Scripts/python.exe -m pytest -q` → full suite green (this task adds a parser + literals only; no behaviour change).

- [ ] **Step 5: Commit**

```bash
git add saturn/models.py saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): prose scenario-math parser + two coherence literals"
```

---

### Task 2: the `prose_arithmetic` check

**Files:**
- Modify: `saturn/agents/synthesist.py` (`scenario_coherence`, appended after the bull_below_spot block, before `return issues`)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_synthesist.py`. NOTE the fixture: legs are given `value`/`mult` matching a real MSFT-like table so ONLY the arithmetic check can fire (the leg-match check arrives in Task 4).

```python
def _msft_legs():
    # prices monotonic; bull above spot; (value, multiple) pairs are the table's truth
    return [_priced_leg("bull", 528.00, 0.33, value=22.0, mult=24.0),
            _priced_leg("base", 461.25, 0.17, value=20.5, mult=22.5),
            _priced_leg("bear", 351.50, -0.11, value=18.5, mult=19.0)]


def test_prose_arithmetic_flags_false_math():
    # 20.5 x 22.5 = 461.25, but the prose claims $358 -> the LLM's own arithmetic is false
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $358.")
    assert [i.check for i in scenario_coherence(t, _dossier())] == ["prose_arithmetic"]


def test_prose_arithmetic_passes_when_correct():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $461.")
    assert scenario_coherence(t, _dossier()) == []      # 461 vs 461.25 is rounding


def test_prose_arithmetic_skips_pair_without_a_price():
    t = _coh_thesis(_msft_legs(), rationale="our base rests on 20.5 EPS × 22.5 P/E across the cycle.")
    assert scenario_coherence(t, _dossier()) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k prose_arithmetic -v`
Expected: FAIL — `test_prose_arithmetic_flags_false_math` gets `[]` (the check doesn't exist).

- [ ] **Step 3: Implement**

In `scenario_coherence`, insert this immediately BEFORE the final `return issues`:
```python
    # 5. Prose arithmetic — the LLM's own "A EPS × B P/E … $C" must actually multiply out. Verifying its
    # stated math needs no whitelist: legitimately-sourced figures (spot, target, driver EPS, RPO) are
    # never touched because they are not part of an asserted pair-and-price claim.
    claims = _prose_math_claims(thesis.variant) + _prose_math_claims(thesis.rationale)
    for a, b, c, _s, _e in claims:
        if c is None:
            continue
        product = a * b
        if product > 0 and abs(product - c) / product > _PROSE_MATH_TOL:
            issues.append(CoherenceIssue(
                check="prose_arithmetic", severity="medium",
                detail=(f"prose states {a:g} × {b:g} ≈ ${c:,.2f}, but the product is ${product:,.2f}")))
            break   # one arithmetic issue per thesis is enough
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k prose_arithmetic -v` → PASS (3).
Then: `.venv/Scripts/python.exe -m pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): prose_arithmetic coherence check"
```

---

### Task 3: `align_prose_scenario_math` corrector + both call sites

**Files:**
- Modify: `saturn/agents/synthesist.py` (new corrector after `align_prose_base_return`; call in `_build_thesis`)
- Modify: `saturn/workflows/equity_research.py` (import; call at the end-of-run recompute)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

```python
from saturn.agents.synthesist import align_prose_scenario_math


def test_align_prose_scenario_math_corrects_the_price():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $358.")
    align_prose_scenario_math(t)
    assert "$461.25" in t.rationale and "$358" not in t.rationale
    assert not any(i.check == "prose_arithmetic" for i in scenario_coherence(t, _dossier()))


def test_align_prose_scenario_math_noop_when_correct():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E → $461.")
    align_prose_scenario_math(t)
    assert "$461." in t.rationale


def test_align_prose_scenario_math_noop_without_a_pair():
    t = _coh_thesis(_msft_legs(), rationale="The base case is cautious.")
    align_prose_scenario_math(t)
    assert t.rationale == "The base case is cautious."


def test_align_prose_scenario_math_corrects_the_variant_field_too():
    t = _coh_thesis(_msft_legs(), rationale="")
    t.variant = "Base 20.5 EPS × 22.5 P/E implies $358."
    align_prose_scenario_math(t)
    assert "$461.25" in t.variant


def test_build_thesis_wires_scenario_math_alignment():
    # guards that align_prose_scenario_math is actually CALLED in _build_thesis — the unit tests above
    # would still pass if the call were deleted.
    from saturn.agents.synthesist import _build_thesis, _resolve_anchor
    from saturn.models import Quote
    d = _dossier(quote=Quote(price=400.0, provenance=Provenance(source="yfinance")))
    data = {"stance": "unclear", "variant": "v",
            "rationale": "base: 20.5 EPS × 22.5 P/E, an implied price near $358.",
            "confidence": "low", "key_variable": "k", "falsifier": "f", "horizon": "12m",
            "scenarios": [
                {"name": "bull", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 22.0, "multiple": 24.0, "multiple_basis": "P/E"},
                {"name": "base", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 20.5, "multiple": 22.5, "multiple_basis": "P/E"},
                {"name": "bear", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 18.5, "multiple": 19.0, "multiple_basis": "P/E"}]}
    t = _build_thesis(data, _resolve_anchor(d), d)
    assert "$461.25" in t.rationale and "$358" not in t.rationale
    assert not any(i.check == "prose_arithmetic" for i in t.coherence_issues)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "scenario_math" -v`
Expected: FAIL — `ImportError: cannot import name 'align_prose_scenario_math'`.

- [ ] **Step 3: Implement**

(3a) Add immediately AFTER `align_prose_base_return` in `saturn/agents/synthesist.py`:
```python
def align_prose_scenario_math(thesis: AlphaThesis) -> None:
    """Correct a stated scenario price in the prose to the product of the LLM's OWN cited value and
    multiple, in place. The LLM keeps its assumptions; code owns the multiplication — so a corrected
    claim lands on the table's price. Uses the same parser/tolerance as the prose_arithmetic check.
    No-ops when there is no cited pair, no claimed price, or the arithmetic is already right."""
    def _fix(text: str) -> str:
        for a, b, c, s, e in _prose_math_claims(text):
            if c is None:
                continue
            product = a * b
            if product > 0 and abs(product - c) / product > _PROSE_MATH_TOL:
                return text[:s] + f"{product:,.2f}" + text[e:]      # first bad claim; span excludes "$"
        return text

    thesis.variant = _fix(thesis.variant)
    thesis.rationale = _fix(thesis.rationale)
```

(3b) In `_build_thesis`, add the call immediately after the existing `align_prose_base_return(thesis)`:
```python
    align_prose_scenario_math(thesis)
```

(3c) In `saturn/workflows/equity_research.py`, add `align_prose_scenario_math` to the `from saturn.agents.synthesist import (...)` list, and at the end-of-run recompute add it right after the existing `align_prose_base_return(alpha)` line:
```python
        align_prose_scenario_math(alpha)     # re-correct any scenario math the prose-repair rewrote
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "scenario_math" -v` → PASS (5).
Then: `.venv/Scripts/python.exe -m pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py saturn/workflows/equity_research.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): deterministically correct prose scenario math"
```

---

### Task 4: the `prose_scenario_not_in_table` check

**Files:**
- Modify: `saturn/agents/synthesist.py` (`scenario_coherence`, after the prose_arithmetic block, before `return issues`)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_prose_scenario_not_in_table_flags_an_orphan_pair():
    # The real MSFT sin: 18.86 x 19 = 358.34 ~= $358, so the ARITHMETIC IS TRUE -- but 18.86x19 is not
    # a leg in the table. This is the check that catches a smuggled second base case.
    t = _coh_thesis(_msft_legs(), rationale="an alternative read: 18.86 EPS × 19x = $358.")
    checks = [i.check for i in scenario_coherence(t, _dossier())]
    assert "prose_scenario_not_in_table" in checks
    assert "prose_arithmetic" not in checks          # the math itself is correct


def test_prose_scenario_not_in_table_tolerance_is_one_percent():
    # REGRESSION GUARD: 18.86 sits 1.95% from the bear leg's 18.5. At a 2% tolerance it would be
    # matched to bear and escape. It must NOT be.
    t = _coh_thesis(_msft_legs(), rationale="an alternative read: 18.86 EPS × 19x = $358.")
    issue = next(i for i in scenario_coherence(t, _dossier()) if i.check == "prose_scenario_not_in_table")
    assert "18.86" in issue.detail


def test_prose_scenario_in_the_table_passes():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E → $461.")
    assert scenario_coherence(t, _dossier()) == []


def test_prose_scenario_tolerates_rounding_of_a_real_leg():
    legs = [_priced_leg("bull", 528.00, 0.33, value=22.0, mult=24.0),
            _priced_leg("base", 461.25, 0.17, value=20.5, mult=22.5),
            _priced_leg("bear", 358.34, -0.10, value=18.86, mult=19.0)]
    # prose rounds 18.86 -> 18.9 (0.21% off) and 18.9*19 = 359.1 vs the stated $359 (0.03%)
    t = _coh_thesis(legs, rationale="bear: 18.9 EPS × 19x → $359.")
    assert scenario_coherence(t, _dossier()) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "not_in_table or tolerates_rounding" -v`
Expected: FAIL — the check doesn't exist, so the orphan pair produces no issue.

- [ ] **Step 3: Implement**

In `scenario_coherence`, insert immediately BEFORE the final `return issues` (after the prose_arithmetic block, so it reuses the `claims` list already computed there):
```python
    # 6. Prose scenario not in the table — a cited (value, multiple) must be one the table actually
    # prices. Catches arithmetic that is TRUE but describes a scenario we never modelled (a smuggled
    # second base case). Tolerance is deliberately tight (_PROSE_LEG_TOL): a looser 2% would match a
    # near-miss like 18.86x19 to a real 18.5x19 leg and let it through.
    legs_vm = [(s.per_share_value, s.multiple) for s in thesis.scenarios]
    for a, b, _c, _s, _e in claims:
        if not any(v > 0 and m > 0
                   and abs(v - a) / v <= _PROSE_LEG_TOL and abs(m - b) / m <= _PROSE_LEG_TOL
                   for v, m in legs_vm):
            issues.append(CoherenceIssue(
                check="prose_scenario_not_in_table", severity="medium",
                detail=f"prose cites {a:g} × {b:g}, which is not a scenario in the table"))
            break   # one orphan report per thesis is enough
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -k "not_in_table or tolerates_rounding" -v` → PASS (4).
Then: `.venv/Scripts/python.exe -m pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): flag prose citing scenario math absent from the table"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `.venv/Scripts/python.exe -m pytest -q`.
- [ ] Offline sanity (no LLM) — reproduce both real MSFT sins and confirm each is handled:
  ```
  .venv/Scripts/python.exe -c "
  from saturn.agents.synthesist import scenario_coherence, align_prose_scenario_math
  from saturn.models import AlphaThesis, ExpectationAnchor, Provenance, ScenarioLeg, CompanyDossier
  from datetime import date
  P=Provenance(source='Saturn (synthesist)')
  def leg(n,v,m,p,r): return ScenarioLeg(name=n, period='FY', driver='d', metric='EPS', metric_basis='adjusted', per_share_value=v, multiple=m, multiple_basis='P/E', implied_price=p, implied_return_pct=r)
  legs=[leg('bull',22.0,24.0,528.0,0.33), leg('base',20.5,22.5,461.25,0.17), leg('bear',18.5,19.0,351.5,-0.11)]
  d=CompanyDossier(ticker='MSFT', name='MSFT', generated_at=date(2026,7,15))
  t=AlphaThesis(anchor=ExpectationAnchor(source='consensus', text='c', confidence='medium'), stance='unclear',
                rationale='base: 20.5 EPS x 22.5 P/E near \$358. An alternative read: 18.86 EPS x 19x = \$358.',
                scenarios=legs, provenance=P)
  align_prose_scenario_math(t); print('CORRECTED:', t.rationale)
  print('ISSUES  :', [(i.check,i.severity) for i in scenario_coherence(t,d)])"
  ```
  Expect the first `$358` rewritten to `$461.25`, and `prose_scenario_not_in_table` flagged for `18.86 × 19`.
- [ ] Live (optional, ~10 min — build the dossier once and pass it to `run()` to dodge yfinance flakiness): regenerate MSFT; confirm no `$358`-style contradiction survives and any orphan pair is named in the §2 banner.

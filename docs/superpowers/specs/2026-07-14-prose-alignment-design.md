# Deterministic Prose-Alignment + Monotonicity-Only Repair — Design

**Date:** 2026-07-14
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** the scenario-coherence gate (PR #35) + slice 2 stronger repair (PR #36), both merged.

## 1. Goal & honest framing

The `prose_vs_computed` coherence issue fires when the alpha thesis's prose states a base-case return
(e.g. "+6%") that contradicts the computed base scenario (e.g. −47%). Slice 2 tried to fix this with a
stronger LLM re-synthesis (horizon rule + arithmetic hint + 2 passes); a verified MRVL run showed it
**still fails** — the LLM keeps writing a mild prose number over a bearish table.

Insight from that run: the LLM's *qualitative argument* is usually already correct (MRVL's rationale
was bearish — "consensus net margin unsupported… below consensus"); only the stated *figure* is wrong.
And the correct figure is already known and shown deterministically (the stance line renders
"base −47% vs consensus target +14%"). So the fix is not more LLM prompting — it is to **correct the
prose figure in code**, the same way `stance` is derived deterministically.

Separately, LLM re-synthesis has proven ineffective at fixing `bull_below_spot`/`multiple_horizon` on
genuinely-bearish names (where every scenario legitimately lands below spot). So this slice also stops
wasting re-synthesis passes on them: **re-synthesis fires only for `monotonicity`** (a gross ordering
error the LLM can actually fix); everything else is either fixed deterministically (prose) or accepted
as an honest banner.

## 2. Part 1 — `align_prose_base_return` (`saturn/agents/synthesist.py`)

A pure deterministic corrector:

```python
def align_prose_base_return(thesis: AlphaThesis) -> None:
    """Correct a stated base-case return in the prose to match the computed base scenario, in place.
    Mirrors the deterministic stance derivation: the LLM owns the argument, code owns the number. Uses
    the same cue/tolerance as the prose_vs_computed check, so after this runs that check cannot fire on
    a parseable, divergent figure. No-ops when there is no base leg, no computed return, no cue, or the
    figure is already within tolerance."""
    base = next((s for s in thesis.scenarios if s.name == "base"), None)
    if base is None or base.implied_return_pct is None:
        return
    computed = f"{base.implied_return_pct * 100:+.0f}"          # e.g. "-47" or "+12"

    def _fix(text: str) -> str:
        def _sub(m):
            return m.group(0).replace(m.group(1), computed, 1)  # swap only the captured number
        # only rewrite when the parsed figure actually diverges beyond tolerance
        m = _PROSE_RETURN_RE.search(text)
        if m and abs(float(m.group(1)) / 100.0 - base.implied_return_pct) > _PROSE_RETURN_TOL:
            return _PROSE_RETURN_RE.sub(_sub, text, count=1)
        return text

    thesis.variant = _fix(thesis.variant)
    thesis.rationale = _fix(thesis.rationale)
```

Notes:
- Replaces only the captured numeric group, preserving `~`, sign, `%`, and surrounding prose. On MRVL:
  `"…implies ~+6% vs the Street's +14%, below because…"` → `"…implies ~-47% vs the Street's +14%,
  below because…"` — the number now matches both the table and the already-bearish argument.
- Both `variant` and `rationale` are corrected (the check inspects `rationale or variant`; correcting
  both leaves no stale figure anywhere in the prose).
- Mutates in place (the callers own the thesis object); pydantic models here are mutable (the codebase
  already assigns `thesis.incompleteness = …`).

**Call site 1 — `_build_thesis`:** insert immediately before the existing
`thesis.incompleteness = alpha_completeness(thesis)` line, so both the completeness gate and
`scenario_coherence` see the corrected prose:

```python
    align_prose_base_return(thesis)
    thesis.incompleteness = alpha_completeness(thesis)
    thesis.coherence_issues = scenario_coherence(thesis, dossier)
    return thesis
```

## 3. Part 2 — monotonicity-only repair trigger (`saturn/workflows/equity_research.py`)

Change the gate loop condition (currently `while alpha is not None and alpha.coherence_issues and
attempts < _MAX_COHERENCE_REPAIRS:`) to fire only when a `monotonicity` issue remains:

```python
    while (alpha is not None
           and any(i.check == "monotonicity" for i in alpha.coherence_issues)
           and attempts < _MAX_COHERENCE_REPAIRS):
        attempts += 1
        r_alpha = resynthesize_coherent(analysis, deb, company, llm, alpha.coherence_issues, model=call_model)
        if r_alpha is None or _coherence_score(r_alpha) >= _coherence_score(alpha):
            break
        alpha = r_alpha
```

The loop body (re-synthesize, keep-if-strictly-better, break on no-improvement) is unchanged. Now:
`prose_vs_computed` is already gone (fixed in `_build_thesis`); `monotonicity` triggers re-synthesis;
`bull_below_spot`/`multiple_horizon` do not trigger and simply render in the banner.

**Call site 2 — end-of-`run()` recompute:** align before the existing recompute (line ~445–446), so a
rationale rewritten by the alpha-repair loop is re-corrected before its coherence is recomputed:

```python
    if alpha is not None:
        align_prose_base_return(alpha)
        alpha.coherence_issues = scenario_coherence(alpha, company)
```

Add `align_prose_base_return` to the `from saturn.agents.synthesist import (...)` line in
`equity_research.py`.

## 4. What stays

- The `prose_vs_computed` **check is kept** as a backstop: if a future prose form isn't matched by the
  corrector's regex, the check still catches it and banners. Defense-in-depth, zero cost.
- The banner render, the `_coherence_score` weights, `_MAX_COHERENCE_REPAIRS`, and the other three
  checks are unchanged.

## 5. Testing

- **`align_prose_base_return` (unit, no LLM):**
  - rationale "Our base case implies ~+6% vs the Street's +14%." + a base leg with
    `implied_return_pct = -0.47` → rationale becomes "…implies ~-47% vs the Street's +14%."; the check
    `scenario_coherence` then returns no `prose_vs_computed`.
  - within-tolerance figure (prose "-40%" vs computed −47%, 7pp ≤ 15pp) → unchanged.
  - no cue in prose ("cautious execution-dependent view") → unchanged.
  - no base leg / base `implied_return_pct is None` → unchanged (no crash).
  - a figure in the **variant** (not just rationale) is corrected too.
  - positive computed return formats with a sign (e.g. base +0.12 over prose "-5%" → "+12%").
- **`_build_thesis` integration:** a thesis built from LLM data whose prose states a divergent base
  return has NO `prose_vs_computed` in `coherence_issues` (it was corrected first); a monotonicity or
  bull_below_spot issue still surfaces normally.
- **Monotonicity-only trigger (`run()` integration, stub LLM):**
  - a table whose only issues are `bull_below_spot`/`multiple_horizon` (no monotonicity) → **0**
    re-synthesis calls; the issues remain and would banner.
  - a non-monotonic table → re-synthesis fires (as before), and a re-synthesis that fixes monotonicity
    is kept.
  - reuse the slice-2 `_MultiPassLLM`/`_mp_dossier` harness; assert `llm.resynth` counts.
  - **Revise the existing slice-2 multipass tests for the new trigger.** Under "any issue" they used
    `bull_below_spot`-driven sequences; those must become `monotonicity`-driven. Specifically:
    `test_run_multipass_two_passes_to_coherent` must make monotonicity persist through pass 1 and be
    fixed in pass 2 (e.g. initial non-monotonic + bull-below-spot → pass1 non-monotonic only → pass2
    coherent), so 2 passes still run; `test_run_multipass_caps_at_two_even_if_still_improving` must
    keep monotonicity present across both passes so the cap (not "monotonicity resolved") is the
    binding stop. `stops_when_no_improvement` and `already_coherent_no_resynth` are unaffected
    (initial has / lacks monotonicity respectively).
- **Live:** regenerate MRVL — confirm the §2 prose now reads a base return consistent with the table
  (≈−47%), `prose_vs_computed` is gone, the banner shows only `[multiple_horizon, bull_below_spot]`,
  and **no** re-synthesis passes ran. Regenerate AVGO — confirm still coherent, no banner, unchanged.

## 6. Scope

- **Modify:** `saturn/agents/synthesist.py` (`align_prose_base_return` + `_build_thesis` call),
  `saturn/workflows/equity_research.py` (monotonicity-only trigger + end-of-run align + import);
  touched tests. **No model change, no render change.**

## 7. Out of scope

- Aligning the qualitative *framing* of the prose (only the stated number is corrected; a residual
  tone mismatch is not gated and not addressed here).
- Repairing `bull_below_spot`/`multiple_horizon` — accepted as an honest banner for genuinely-bearish
  tables.
- Any change to the stance derivation, the anchor, the driver model, or `SYNTHESIZE_SYSTEM`.
- Deleting the `prose_vs_computed` check (kept as a backstop).

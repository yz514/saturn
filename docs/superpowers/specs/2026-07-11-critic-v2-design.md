# Critic-v2: Self-Repair Loop — Design

**Date:** 2026-07-11
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Make the Critic **correct** the errors it catches, not just flag them. v1 is advisory — the
wrong numbers still sit in the prose above the §14 Verification section. v2 adds a
**self-repair loop**: revise the affected sections, re-verify, and keep the correction only
if it provably improves.

## 2. Flow (in `run()`)

```
analyze → debate → critique → [if actionable findings] → revise → re-critique → keep-if-better → render
```

1. **`_actionable(review)`** — trigger repair only for **high/medium** findings of category
   `contradiction | unsupported_number | over_weighting`. Low-severity and `unverified_claim`
   stay advisory (not worth a repair pass).
2. **`revise(...)`** — one LLM call given the affected sections' current text + the specific
   findings (each with the report's own data as evidence) + the dossier. Returns **corrected
   text for only the flagged sections** (`{section: new_text}`). Instruction: fix exactly the
   cited problems; preserve everything else verbatim; add no new claims.
3. Splice via `model_copy(update=...)` — **unaffected sections stay verbatim (deterministic)**,
   so revise can't quietly damage good content.
4. **`critique(revised)`** — re-run the Critic on the corrected report.
5. **Safety gate** — accept the revision **only if `_score(revised) < _score(original)`**
   (severity-weighted: high=3, medium=2, low=1). Otherwise keep the original. Soft-fail
   throughout (revise/re-critique failure → keep original).

## 3. Data model & helpers

- `CriticReview.repaired: bool = False`.
- In `saturn/agents/critic.py`:
  - `_is_actionable_finding(f)` = `f.severity in ("high","medium") and f.category in ("contradiction","unsupported_number","over_weighting")`.
  - `_actionable(review)` = any actionable finding.
  - `_score(review)` = `sum({"high":3,"medium":2,"low":1}.get(f.severity,1) for f in review.findings)`.
  - `REVISE_SYSTEM` prompt + `revise(analysis, debate, review, dossier, llm, *, model=None) -> dict[str,str] | None`
    (only the affected sections; soft-fail to None; resilient JSON parse like `critique`).

## 4. Rendering (§14)

The final `CriticReview` reflects what **remains** after (any) repair. `repaired` drives the note:
- `repaired and not findings` → "_Auto-corrected by the Critic (self-repair); no issues remain._"
- `repaired and findings` → "_Auto-corrected by the Critic; N issue(s) remain below._"
- `not repaired` → the existing advisory note.

## 5. Workflow integration (`run()`)

```python
review = critique(analysis, deb, company, llm, model=call_model)
if review is not None and _actionable(review):
    corrections = revise(analysis, deb, review, company, llm, model=call_model)
    if corrections:
        r_analysis = analysis.model_copy(update={k: v for k, v in corrections.items() if k in AnalysisSections.model_fields})
        r_deb = deb.model_copy(update={k: v for k, v in corrections.items() if k in DebateSections.model_fields})
        r_review = critique(r_analysis, r_deb, company, llm, model=call_model)
        if r_review is not None and _score(r_review) < _score(review):
            analysis, deb, review = r_analysis, r_deb, r_review
            review.repaired = True
```

## 6. Cost & edges

- **+2 LLM calls (revise + re-critique) ONLY when actionable findings exist**; a clean report
  incurs nothing extra.
- **Mock path:** the mock Critic returns 0 findings → not actionable → revise never runs (no
  mock change needed).
- **Re-critique non-determinism:** handled by the strict `<` score comparison (accept only if
  provably better; ties/regressions keep the original).
- Revise never rewrites unaffected sections (spliced deterministically), bounding damage.

## 7. Testing

- **Helpers:** `_score` / `_actionable` on synthetic reviews; `_is_actionable_finding` category/severity matrix.
- **`revise`:** mock LLM returns corrected sections → returns the dict; malformed JSON → None (soft-fail); only affected sections returned.
- **`run()` self-repair:** a stateful stub LLM (analyze → debate → critic-with-1-actionable-finding → revise-correction → re-critic-with-fewer-findings) → the report's affected section is corrected and `critic_review.repaired is True`; a variant where re-critique does NOT improve → original kept, `repaired is False`.
- **Render:** `repaired=True` → §14 note present.
- **Live:** regenerate a report that has an actionable finding (e.g. MU's segment-margin or capex contradiction) → confirm the prose is corrected and §14 says auto-corrected.

## 8. Scope

- **Modify:** `saturn/models.py` (`CriticReview.repaired`), `saturn/agents/critic.py`
  (`REVISE_SYSTEM`, `revise`, `_is_actionable_finding`, `_actionable`, `_score`),
  `saturn/workflows/equity_research.py` (the loop in `run()`), `saturn/reports/markdown_report.py`
  (§14 note), touched tests.

## 9. Out of scope

- A hard gate that blocks/withholds the report (we correct instead).
- Multi-round repair (single revise pass; a second round is a later option).
- Perspective-diverse critic panel (still v3).

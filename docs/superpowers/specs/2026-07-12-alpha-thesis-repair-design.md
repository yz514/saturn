# Alpha-Thesis Auto-Repair — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** the alpha frame (v1.0 #27) + v1.1 (#28) + v1.2 (#30), all merged to `main`.

## 1. Goal

Extend the Critic self-repair loop to the **alpha thesis**. Today self-repair (`revise` +
keep-if-better in `run()`) only edits analysis/debate sections; the structured `AlphaThesis` is
excluded, so a Critic finding on it is *surfaced* (§1 banner, §15) but never *corrected*. In a
verified JNJ run the alpha rationale said *"trailing 3-year FCF CAGR of near zero"* when the data
is a **~3.9% 2-year CAGR** (and the report stated that correctly elsewhere) — a real HIGH
`unsupported_alpha_inference` that self-repair could not fix. This adds an alpha-repair pass so
such errors are auto-corrected under the same keep-if-better safety gate.

## 2. Guardrail — prose only

Alpha-repair rewrites **only the LLM prose fields**. Add a module constant in `saturn/models.py`
next to `AlphaThesis`:

```python
ALPHA_PROSE_FIELDS = ("variant", "rationale", "key_variable", "falsifier", "horizon")
```

`stance`, `stance_basis`, `anchor`, `scenarios`, and `incompleteness` are **never** rewritten by
the LLM — `stance`/`stance_basis` are deterministically derived (v1.1/v1.2), `scenarios`/`anchor`
carry code-computed prices, and `incompleteness` is a gate artifact. This preserves the epistemic
boundary the whole layer rests on.

## 3. New pieces in `saturn/agents/critic.py`

- **Trigger helpers:**
  - `_is_alpha_actionable(f)` = `f.severity in ("high", "medium") and (f.section or "").startswith("alpha_thesis")`.
  - `_alpha_actionable(review)` = `any(_is_alpha_actionable(f) for f in review.findings)`.
  - (`unsupported_alpha_inference` stays OUT of `_ACTIONABLE_CATEGORIES` so it never triggers the
    analysis/debate `revise` path — alpha findings are handled only by this new path.)
- **`REVISE_ALPHA_SYSTEM`** — instruction: given specific VERIFIED problems on the alpha thesis
  and the current prose fields, rewrite ONLY those prose fields to fix exactly the cited problems
  using the data; do NOT change the stance, the scenario numbers/assumptions, or invent figures;
  respond with ONLY a JSON object mapping the affected prose field names to corrected text.
- **`revise_alpha(alpha, dossier, findings, llm, *, model=None) -> dict | None`** — builds the
  prompt (problems from `findings` + current `{f: getattr(alpha, f) for f in ALPHA_PROSE_FIELDS}` +
  `_company_context(dossier)`), calls the LLM, parses resiliently, and returns
  `{prose_field: corrected_text}` filtered to `ALPHA_PROSE_FIELDS` (any `stance`/`scenarios` key
  the model returns is dropped). Soft-fails to `None`. Mirrors the existing `revise()` shape and
  resilience (one attempt is fine; `revise()` itself does not retry — match it).

## 4. Applying corrections — `saturn/agents/synthesist.py`

`apply_alpha_corrections(alpha, corrections) -> AlphaThesis`:
- `alpha.model_copy(update={k: v for k, v in corrections.items() if k in ALPHA_PROSE_FIELDS})`.
- Recompute `incompleteness = alpha_completeness(new_alpha)` (prose changed → gate may change).
- Return the new thesis. Stance/stance_basis/anchor/scenarios are carried over verbatim by
  `model_copy`. Synthesist owns `AlphaThesis` structure; the critic only *generates* corrections.

## 5. Wiring — `run()` in `saturn/workflows/equity_research.py`

A second repair loop AFTER the existing section-repair block (which is unchanged):

```python
if review is not None and alpha is not None and _alpha_actionable(review):
    alpha_corr = revise_alpha(
        alpha, company,
        [f for f in review.findings if _is_alpha_actionable(f)],
        llm, model=call_model,
    )
    if alpha_corr:
        r_alpha = apply_alpha_corrections(alpha, alpha_corr)
        r_review = critique(analysis, deb, company, llm, model=call_model, alpha=r_alpha)
        if r_review is not None and _score(r_review) < _score(review):
            r_review.repaired = True
            alpha, review = r_alpha, r_review
```

**Sequential, independent gate.** The alpha loop runs against the possibly-already-section-repaired
`analysis`/`deb`/`review`, with its own strict `_score(r_review) < _score(review)` gate, so a
section fix and an alpha fix are judged independently and neither can drag the other. Because
`_score` sums severity weights over *all* findings (including `unsupported_alpha_inference`),
removing a high alpha finding drops the score by 3 and the gate accepts; the §1 banner then shows
nothing for it. Imports to add in `equity_research.py`: `revise_alpha`, `_alpha_actionable`,
`_is_alpha_actionable` from `critic`; `apply_alpha_corrections` from `synthesist`.

## 6. Cost

+2 LLM calls (revise_alpha + re-critique) **only when an actionable alpha finding exists**. Worst
case +4 when both a section finding and an alpha finding fire in the same report; most reports add
0 or +2. Mock path (0 findings) adds nothing.

## 7. Testing

- **`_is_alpha_actionable` matrix:** high/medium on `alpha_thesis*` → True; low on alpha → False;
  high on a non-alpha section → False.
- **`revise_alpha`:** mock LLM returns corrected prose (plus a stray `stance` key) → returns only
  the `ALPHA_PROSE_FIELDS` corrections, drops `stance`; malformed JSON → `None`.
- **`apply_alpha_corrections`:** prose spliced; `stance`, `stance_basis`, `anchor`, `scenarios`
  unchanged; `incompleteness` recomputed from the new prose.
- **`run()` alpha-repair:** a stateful stub LLM (analyze → debate → synthesize → critique with one
  high `unsupported_alpha_inference` on `alpha_thesis` → revise_alpha correction → clean
  re-critique) → the alpha prose is updated and `critic_review.repaired is True`; a variant where
  the re-critique does NOT improve → original alpha kept, not repaired.
- **Live:** regenerate JNJ; confirm the "3-year CAGR near zero" rationale is auto-corrected (to
  the ~3.9% 2-year figure) and the §1 banner for that finding is gone.

## 8. Scope

- **Modify:** `saturn/models.py` (`ALPHA_PROSE_FIELDS`), `saturn/agents/critic.py`
  (`_is_alpha_actionable`, `_alpha_actionable`, `REVISE_ALPHA_SYSTEM`, `revise_alpha`),
  `saturn/agents/synthesist.py` (`apply_alpha_corrections`),
  `saturn/workflows/equity_research.py` (second repair loop + imports); touched tests.

## 9. Out of scope

- Repairing the compound **stance-vs-Final-View** findings by rewriting the Final View — alpha
  prose repair can't fix those; the gate rejects the attempt and the §1 banner still surfaces
  them. A later refinement can route such findings to Final-View (debate-section) repair.
- Any rewriting of `scenarios`, prices, `stance`, or `anchor`.
- A multi-round alpha repair (single pass, mirroring the section loop).

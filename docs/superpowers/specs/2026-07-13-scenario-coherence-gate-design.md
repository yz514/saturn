# Scenario-Coherence Gate (Alpha Slice) — Design

**Date:** 2026-07-13
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** the alpha frame (v1.0 #27 → v1.2 #30 + alpha-repair #31) and the driver-model
NTM horizon fix (commit `c635728`, branch `consensus-revenue-waterfall`), all on/toward `main`.

## 1. Goal & honest framing

The alpha layer prices scenarios deterministically (`implied_price = per_share_value × multiple`)
from **LLM-supplied** `(per_share_value, multiple)` pairs, but nothing checks the resulting table
for internal coherence. On a post-fix MRVL run the report was internally self-contradictory:
the variant prose said the base case implied "~+2%" while the table priced base at $136.80
(**−42%**), the "bull" case was priced **below spot** (−15%), and the Critic — which audits
individual numbers against source data — flagged none of it. An earlier MRVL run happened to pick
coherent numbers ($4.5 × 53 = $238, +1%); the difference was pure LLM sampling.

This slice adds a **deterministic coherence gate** on the priced scenario table. When it fires,
Saturn does **one corrective re-synthesis** and keeps the result only if it is *strictly more
coherent*; any residual incoherence renders as a prominent warning banner. The gate never rewrites
the LLM's numbers itself (that would fabricate judgment the analyst never reasoned to) — it detects,
optionally re-asks, and otherwise surfaces the problem honestly.

**Cost:** 0 LLM calls when the table is coherent (the common case); **+1 LLM call** (one
`synthesize`) only when the gate fires. The mock path produces coherent-or-empty scenarios and adds
nothing.

## 2. The three checks (deterministic)

Computed on the **priced** scenarios (`implied_price`, `implied_return_pct` already populated by
`_price_scenarios`). Each check yields zero or one `CoherenceIssue`.

- **Monotonicity** `[high]` — with the three legs resolved by name, require
  `bull.implied_price ≥ base.implied_price ≥ bear.implied_price`. Any inversion (e.g. a bull worth
  less than the bear) → issue. Only evaluated when all three legs exist and are priced; otherwise
  skip (the completeness gate already flags a missing/short scenario set).

- **Prose-vs-computed** `[medium]` — the v1.2 synthesist prompt instructs the LLM to write the
  rationale around "our base case implies +X% vs the Street's +Y%". Parse the **first** signed
  percentage that follows a `base case implies` cue (case-insensitive, tolerating a leading `~`) in
  `rationale`, falling back to `variant`. If a number is found and
  `abs(parsed_fraction − base.implied_return_pct) > 0.15` (15 percentage points) → issue. If no cue
  or no number is parseable, or the base leg has no `implied_return_pct` → **no issue** (never a
  false positive from an unparseable string).

- **Multiple-horizon** `[medium]` — the mechanistic root cause: applying the forward (FY+1) P/E to a
  materially lower near-term EPS. For any leg with `multiple_basis == "P/E"`, **when**
  `dossier.consensus` is present with both `forward_pe` and `forward_eps` not None: if
  `abs(leg.multiple − forward_pe) ≤ 0.15 × forward_pe` **and**
  `leg.per_share_value < 0.8 × forward_eps` → issue. When consensus / either field is absent, skip
  (no baseline to judge the horizon).

Severity weights for the keep-if-better gate: `high = 2`, `medium = 1`.

## 3. Data model (`saturn/models.py`)

New structured type (small, so severity survives to the gate and the banner):

```python
class CoherenceIssue(BaseModel):
    check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon"]
    severity: Literal["high", "medium"]
    detail: str   # human-readable, e.g. "bull $201.60 priced below base $... "
```

`AlphaThesis` gains:

```python
    coherence_issues: list[CoherenceIssue] = Field(default_factory=list)
```

Placed after `incompleteness`. It is a **computed/deterministic** field (like `stance`,
`stance_basis`, `incompleteness`) — NOT in `ALPHA_PROSE_FIELDS`, so the prose self-repair never
touches it; it is always recomputed by `scenario_coherence`.

## 4. Detection function (`saturn/agents/synthesist.py`)

```python
def scenario_coherence(thesis: AlphaThesis, dossier: CompanyDossier) -> list[CoherenceIssue]:
    """Deterministic coherence audit of the priced scenario table (sibling to alpha_completeness).
    Returns issues in a stable order: monotonicity, prose_vs_computed, multiple_horizon."""
```

- Resolve legs by name (`bull`/`base`/`bear`) via a dict comprehension over `thesis.scenarios`.
- Run the three §2 checks; append a `CoherenceIssue` per firing check with a specific `detail`.
- Pure function, no LLM, no exceptions on missing data (guards return early → skip that check).

A module constant `_PROSE_RETURN_RE` holds the compiled regex, and `_COHERENCE_MULTIPLE_TOL = 0.15`,
`_COHERENCE_EPS_FLOOR = 0.8`, `_PROSE_RETURN_TOL = 0.15` are module constants (no magic numbers).

Wired into `_build_thesis` right after `thesis.incompleteness = alpha_completeness(thesis)`:

```python
    thesis.coherence_issues = scenario_coherence(thesis, dossier)
```

(`_build_thesis` already receives `dossier`.)

## 5. Corrective re-synthesis (`saturn/agents/synthesist.py`)

```python
def resynthesize_coherent(analysis, debate, dossier, llm, issues, *, model=None) -> AlphaThesis | None:
    """One corrective synthesize pass: re-ask for a fully self-consistent thesis given the specific
    coherence problems. Reuses the synthesize machinery so prose AND scenarios regenerate together
    (a scenarios-only patch would risk re-introducing prose-vs-computed). Soft-fails to None."""
```

- Builds the same prompt as `synthesize` (`_synthesize_prompt` with the resolved anchor +
  `_company_context`), then appends a corrective block listing the issues verbatim, e.g.:
  `"Your previous scenario table failed these coherence checks: <details>. Regenerate the full
  thesis so that: bull ≥ base ≥ bear in implied price; any P/E multiple matches the horizon of its
  EPS (do not apply a next-fiscal-year multiple to a near-term EPS); and the base-case return you
  describe in the rationale matches the base scenario you output. Do NOT output prices."`
- Same resilience as `synthesize`: one retry on unparseable JSON, per-leg validation, `_build_thesis`
  (which re-prices, re-derives stance, recomputes completeness AND coherence). Returns the rebuilt
  `AlphaThesis` or `None`.
- Does **not** call the Critic; coherence is re-judged deterministically by the rebuilt thesis's
  `coherence_issues`.

## 6. Keep-if-more-coherent gate (`saturn/workflows/equity_research.py`)

Immediately AFTER `alpha = synthesize(...)` and BEFORE `review = critique(...)`, so the Critic audits
the more-coherent thesis:

```python
def _coherence_score(a) -> int:
    return sum(2 if i.severity == "high" else 1 for i in a.coherence_issues)

if alpha is not None and alpha.coherence_issues:
    r_alpha = resynthesize_coherent(analysis, deb, company, llm, alpha.coherence_issues, model=call_model)
    if r_alpha is not None and _coherence_score(r_alpha) < _coherence_score(alpha):
        alpha = r_alpha
```

`_coherence_score` lives in `synthesist.py` (next to `scenario_coherence`) and is imported into
`equity_research.py`. **Single pass** — mirrors the existing section/alpha repair (one shot,
keep-if-better). If the re-synthesis is also incoherent, the better-scoring thesis is kept and its
residual issues surface in the banner. A `None` (soft-fail) leaves the original untouched.

## 7. Render (`saturn/reports/markdown_report.py`)

`_render_coherence_banner(alpha) -> list[str]` mirroring `_render_high_severity_banner`: when
`alpha.coherence_issues` is non-empty, emit a prominent block in §2 (Alpha Thesis), before the
scenario table:

```
> ⚠️ **Scenario coherence warning(s)** — treat the scenario returns as provisional:
>   • [monotonicity] bull $201.60 is priced below base $236.00
>   • [prose_vs_computed] rationale says base ~+2% but the table computes -42%
```

When `alpha.coherence_issues` is empty → nothing rendered. Per the approved design there is **no**
confidence auto-capping (flag + re-generate only); the banner is the sole surfacing.

## 8. Testing

- **`scenario_coherence` (unit, no LLM):**
  - monotonic-inversion fixture (bull priced below base) → one `[high] monotonicity` issue; a
    correctly ordered table → none.
  - rationale "base case implies ~+2%" with a computed base return of −0.42 → one `[medium]
    prose_vs_computed`; rationale matching the computed return → none; rationale with no cue / no
    number → none (no false positive); base leg without `implied_return_pct` → none.
  - a `P/E` leg with `multiple ≈ forward_pe` and `per_share_value < 0.8×forward_eps` (consensus
    present) → one `[medium] multiple_horizon`; the same leg with consensus absent → none; a leg
    whose multiple is far from `forward_pe` → none.
  - a fully coherent thesis → empty list; ordering of issues is stable (monotonicity,
    prose_vs_computed, multiple_horizon).
- **`_build_thesis`:** a thesis built from incoherent LLM data has non-empty `coherence_issues`; a
  coherent one has an empty list. (`--mock` path stays empty.)
- **`resynthesize_coherent`:** a stub LLM returning a coherent scenario table → returns a rebuilt
  thesis with `_coherence_score == 0`; malformed JSON twice → `None`.
- **`run()` keep-if-more-coherent:** a stateful stub where the first `synthesize` yields an
  incoherent table and `resynthesize_coherent` yields a coherent one → the report's `alpha_thesis`
  has no coherence issues; a variant where the re-synthesis does NOT improve the score → the original
  (incoherent) thesis is kept and the banner shows its issues; existing synthesist/critic/self-repair
  tests stay green.
- **Render:** banner present (with each residual issue) when `coherence_issues` non-empty; absent
  when empty.
- **Live:** regenerate MRVL and confirm either a coherent scenario table (no banner) or, if the
  re-synthesis could not fix it, a visible coherence banner — and that the base-return prose no
  longer contradicts the table.

## 9. Scope

- **Modify:** `saturn/models.py` (`CoherenceIssue` + `AlphaThesis.coherence_issues`),
  `saturn/agents/synthesist.py` (`scenario_coherence`, `_coherence_score`, `resynthesize_coherent`,
  call in `_build_thesis`, module constants), `saturn/workflows/equity_research.py`
  (keep-if-more-coherent loop + import), `saturn/reports/markdown_report.py`
  (`_render_coherence_banner` + call in §2); touched tests.

## 10. Out of scope

- The **bull-below-spot** check (dropped in brainstorming — redundant once the stance is correctly
  derived as `below_consensus`, where a below-spot bull can be legitimate).
- Deterministic auto-correction of scenario legs (reordering/clamping) — fabricates numbers the
  analyst never reasoned to.
- Confidence auto-capping on residual incoherence, multi-pass repair (this slice is single-pass),
  changing the stance derivation, EV/EBITDA scenario math, or the driver model.
- Routing coherence issues through the Critic's finding/`_score`/banner machinery — coherence is a
  distinct deterministic concern with its own field and banner.

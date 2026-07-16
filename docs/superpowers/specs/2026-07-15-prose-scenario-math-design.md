# Prose Scenario-Math Verification — Design

**Date:** 2026-07-15
**Status:** Approved (brainstorm) → ready for plan
**Builds on:** the coherence gate (#35/#36) + deterministic prose-alignment (#37) + NTM blending (#39).

## 1. Goal & honest framing

The alpha thesis prices scenarios deterministically, but the LLM also writes its **own** price/multiple
math in free prose — unchecked. Only the single `"base case implies X%"` phrase is corrected today
(`align_prose_base_return`). Verified live on MSFT (2026-07-15), whose rationale committed **two
distinct sins** against a table whose base is `20.5 EPS × 22.5 P/E = $461.25`:

| Prose claim | Arithmetic | Verdict |
|---|---|---|
| `"20.5 EPS × 22.5 P/E … yielding an implied price near $358"` | 20.5×22.5 = **461.25 ≠ 358** | **false math** about a real leg |
| `"$18.86 EPS × 19x = $358"` | 18.86×19 = **358.3 ≈ 358** — *true* | **true math** about a **fictional** leg |

The second is the worse one — it smuggles an entire second base case (−9.4%) into a report whose table
says +17%, and it is what made the MSFT alpha unusable. Pure arithmetic-checking sails past it.

**Why not the obvious approach.** "Strip or flag numbers in prose" is a false-positive disaster: of the
15 price tokens in that rationale, **13 are legitimately sourced** — `$395.63` (spot), `$560` (Street
target), `$18.86`/`$16.82` (driver EPS), `$633B` (RPO), `$120B` (capex) — and the prose needs them to
reason. Only `$358` and `23x` are bad. So the checks must be **targeted at asserted scenario math**, not
at numbers generally.

**The insight:** both sins are detectable with **no whitelist and no notion of "legitimate"** — one by
verifying the LLM's own arithmetic, the other by requiring cited scenario math to exist in the table.

## 2. Part 1 — `prose_arithmetic` check + corrector

**Detect.** Find explicit `A EPS × B P/E … $C` claims (this shape mirrors the scenario table's own
**Math** column, `f"{value:g} {metric} × {multiple:g} {multiple_basis}"`, which is exactly how the LLM
restates it). If `abs(A*B − C) / (A*B) > _PROSE_MATH_TOL` (**2%**) → `CoherenceIssue(check="prose_arithmetic", severity="medium")`.

**Correct.** `align_prose_scenario_math(thesis)` replaces the stated `C` with `A*B` — the LLM keeps its
own `A` and `B`; code owns the multiplication. On MSFT: `"20.5 EPS × 22.5 P/E … $358"` → **`$461.25`**,
which *is* the table's base price. Mirrors `align_prose_base_return` exactly (same call sites, same
in-place mutation).

## 3. Part 2 — `prose_scenario_not_in_table` check

**Detect.** Every `(A, B)` pair cited in prose must match some table leg's
`(per_share_value, multiple)` within `_PROSE_LEG_TOL`. No match → `CoherenceIssue(check="prose_scenario_not_in_table", severity="medium")`,
detail naming the orphan pair. Not correctable — a fictional scenario cannot be deterministically
repaired, so flagging is the honest outcome.

**Tolerance is load-bearing — `_PROSE_LEG_TOL = 0.01` (1%), NOT 2%.** Calibrated against the real case:

| prose `18.86 × 19x` vs leg | EPS off | multiple off |
|---|---|---|
| bull `22 × 24` | 14.27% | 20.8% |
| base `20.5 × 22.5` | 8.00% | 15.6% |
| **bear `18.5 × 19`** | **1.95%** | **0.0%** |

At 2% the smuggled pair would **match the bear leg and escape**. At 1% no leg matches → flagged, while a
genuine restatement rounding `18.86 → "18.9"` (0.21%) still passes.

## 4. Implementation

**`saturn/models.py`** — extend the literal:
```python
    check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon", "bull_below_spot",
                   "prose_arithmetic", "prose_scenario_not_in_table"]
```

**`saturn/agents/synthesist.py`** — module constants beside the existing `_PROSE_RETURN_*`:
```python
_PROSE_MATH_TOL = 0.02        # stated A*B may differ from the true product only by rounding
_PROSE_LEG_TOL = 0.01         # a cited (value, multiple) must match a table leg this closely
_PROSE_MATH_LOOKAHEAD = 120   # chars after a cited pair in which to find its claimed price
_PROSE_PAIR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:EPS|FCF/share|sales/share)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:P/E|P/FCF|P/S|x)\b",
    re.IGNORECASE)
_PROSE_PRICE_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
```

A shared pure helper yields the parsed claims so the check and the corrector cannot drift apart:
```python
def _prose_math_claims(text: str) -> list[tuple[float, float, float | None, int, int]]:
    """Every 'A EPS × B P/E' pair in `text`, with the price it claims (if one follows within
    _PROSE_MATH_LOOKAHEAD chars) and that price token's span. Pure; returns [] when none."""
```

- **Checks** live in `scenario_coherence(thesis, dossier)`, appended after the existing four so the
  stable order becomes `[monotonicity, prose_vs_computed, multiple_horizon, bull_below_spot,
  prose_arithmetic, prose_scenario_not_in_table]`. Both scan `variant` and `rationale`.
- **Corrector** `align_prose_scenario_math(thesis) -> None` mutates in place, called immediately after
  `align_prose_base_return` at **both** existing sites: in `_build_thesis` (before
  `alpha_completeness`/`scenario_coherence`) and at the end-of-`run()` recompute.

**No render change** — the §2 coherence banner already displays any `CoherenceIssue`.

## 5. Testing

- **`_prose_math_claims` (unit):** parses `"20.5 EPS × 22.5 P/E … near $358"` → `(20.5, 22.5, 358.0, …)`;
  handles ascii `x` and unicode `×`; a pair with no `$` within 120 chars → price `None`; no pair → `[]`.
- **`prose_arithmetic`:** `20.5 × 22.5 … $358` → one `[medium]` issue; `20.5 × 22.5 … $461` (0.05% off)
  → none; `18.86 × 19 … $358` (true math) → none.
- **`align_prose_scenario_math`:** the `$358` above is rewritten to `$461.25` and the check then finds
  nothing; a claim already correct is untouched; no pair → no-op; no price → no-op (nothing to correct).
- **`prose_scenario_not_in_table`:** with legs `22×24 / 20.5×22.5 / 18.5×19`, prose `18.86 × 19x` → one
  `[medium]` issue (**the 1%-tolerance regression test** — assert it is NOT matched to the bear leg);
  prose `20.5 × 22.5` → none; prose `18.9 × 19` (0.21% off bear's 18.5? no — off 18.86) → use a leg of
  `18.86 × 19` and prose `18.9 × 19` → none (rounding tolerated).
- **`_build_thesis` wiring:** a thesis whose prose states false scenario math has it corrected and no
  `prose_arithmetic` issue (guards that the corrector is actually called — the unit tests alone would
  pass even if the call were deleted).
- **Live:** regenerate MSFT; confirm the rationale's `$358` is gone (reads `$461.25`) and any orphan
  `18.86 × 19x` is flagged in the §2 banner.

## 6. Scope

- **Modify:** `saturn/models.py` (2 literals), `saturn/agents/synthesist.py` (constants, regexes,
  `_prose_math_claims`, two checks in `scenario_coherence`, `align_prose_scenario_math` + its two call
  sites); touched tests. **No render change, no driver/anchor change.**

## 7. Out of scope

- The `"$560 target requires ~23x on $18.86"` form: a claim about the **Street's** target, not a
  restatement of our scenario. Different pattern, more regex surface, less value.
- Parsing arbitrary/creatively-phrased prose math. The regex targets only the explicit
  `A EPS × B P/E … $C` shape the table's Math column induces; exotic phrasings go unchecked (accepted —
  the `prose_vs_computed` and the four existing checks remain as backstops).
- Repairing a fictional scenario (flag only); news filtering, period tagging, Critic upgrades (P2/P3).

# v1.2 Stance/Rationale Alignment — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** alpha-frame-v1.1 (PR #28, branch `alpha-frame-v1.1`). This branch
(`alpha-frame-v1.2`) is stacked on it; merge #28 first, then this.

## 1. Problem

v1.1 made the alpha `stance` **deterministic** — derived from the base-case return vs the
consensus target upside, overriding whatever the LLM declared. But the LLM's **rationale prose**
is written in the same synthesize call, where the current `SYNTHESIZE_SYSTEM` instructs it to
*"State whether the view is above / in line with / below the anchor and WHY"*. So the model
asserts its own overall verdict, which the deterministic override can then contradict.

Observed live (MSFT, v1.1 run): derived stance `below_consensus` (base +23% vs consensus +45%),
but the rationale said the view is *"consistent with consensus rather than differentiated."* The
Critic correctly flagged the contradiction and the §1 banner surfaced it — the safety net worked,
but the report shipped a stance label at odds with its own prose.

## 2. Fix (prompt reframe only — no logic change, no extra LLM call)

Change **`SYNTHESIZE_SYSTEM`** in `saturn/agents/synthesist.py` so the rationale is written on the
same axis the stance is derived on, and the model stops asserting a competing verdict.

**Current text (the clause to replace):**

> "State whether the view is above / in line with / below the anchor and WHY, grounded in specific
> data. If you cannot honestly take a differentiated view, return stance 'unclear' — never
> manufacture one."

**Replacement clause:**

> "Write the RATIONALE around how your base-case scenario's return compares to the anchor — the
> consensus target upside, or the model-implied expectation — e.g. 'our base case implies +X% vs
> the Street's +Y%, below/above because …', grounded in specific data. Do NOT assert an overall
> 'consistent with / differentiated from consensus' verdict and do NOT re-state the stance label
> in prose: the system derives and labels the stance deterministically from your base-case return
> vs consensus. Still return a 'stance' field — it is used only as a fallback when there is no
> consensus target; if you cannot take a differentiated view there, use 'unclear' and never
> manufacture one."

The rest of `SYNTHESIZE_SYSTEM` (key variable, observable falsifier, horizon, three scenarios,
per-share metric+multiple, no prices, variant ≤35 words, JSON-only) is unchanged.

## 3. What does NOT change

- `_derive_stance`, `_build_thesis`, the stance enum, `stance_basis`, and the rendering — all
  untouched. Stance is still derived exactly as in v1.1.
- The JSON schema and `_synthesize_prompt` — unchanged. The `stance` field stays (no-consensus
  fallback); `_synthesize_prompt` already tells the model its declared stance is overridden when a
  consensus target exists, which is consistent with the new system-prompt wording.
- No new LLM call. The Critic's stance-vs-Final-View check and the §1 banner remain the backstop
  for the rare residual mismatch this prompting doesn't prevent.

## 4. Testing

- **Unit (deterministic):** assert `SYNTHESIZE_SYSTEM` now contains the alignment instruction and
  no longer contains the old verdict-assertion clause — specifically that it mentions framing the
  rationale around the base-case return vs the anchor and instructs NOT to assert an overall
  consistent/differentiated verdict. (A content assertion on the constant, mirroring
  `test_critic_prompt_has_stance_vs_final_view_check`.)
- **Existing tests:** `synthesize` behavior is otherwise unchanged, so the full suite stays green.
- **Live (post-implementation):** regenerate the MSFT report and confirm (a) the rationale now
  reads consistently with the derived stance (frames the base-case-vs-consensus comparison rather
  than declaring "consistent with consensus"), and (b) the Critic no longer emits the
  stance-vs-rationale `unsupported_alpha_inference` finding that appeared in the v1.1 run.

## 5. Scope

- **Modify:** `saturn/agents/synthesist.py` (`SYNTHESIZE_SYSTEM` string only) + a prompt-content
  test in `tests/agents/test_synthesist.py`.
- **Out of scope:** a realignment LLM pass on conflict (considered — deferred; the prompt reframe
  attacks the root and the Critic/banner catch residuals), and the fundamental-vs-price two-axis
  stance split (a larger redesign, not needed here).

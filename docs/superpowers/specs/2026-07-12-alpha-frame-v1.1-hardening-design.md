# Alpha-Frame v1.1 Hardening — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user (+ external GPT review)
**Builds on:** the Alpha-Framing Layer (PR #27, branch `alpha-framing`). This branch
(`alpha-frame-v1.1`) is stacked on `alpha-framing`; merge #27 first, then this.

## 1. Goal

Three loosely-coupled fixes that make the alpha frame **trustworthy and unambiguous**,
motivated by a verified `MSFT_2026-07-11.md` report where:
- RPO coverage showed **7.6x** (exec summary) vs **2.2x** (valuation) — a HIGH-severity
  contradiction the Critic flagged but self-repair did **not** fix (the finding was attributed
  to the section holding the *correct* value, so `revise` couldn't improve it); and
- the `stance` "below expectations" (base case still +11%) read as bearish.

The three parts are independent (data layer / stance / backstop) and may land in any order.

**Out of scope** (considered, deferred): probability/expected-value scenario column (declined —
LLM-guessed probabilities are an ungroundable fabrication surface); structured
falsifier+confirmer (belongs to the later thesis-tracking stage); alpha rationale length cap.

---

## 2. Part 1 — RPO coverage ratios (`saturn/analytics/metrics.py`)

**Problem.** `_backlog` emits one metric `rpo_to_revenue = RPO ÷ latest-FY revenue` (~2.2x) whose
`formula` names an ambiguous "Revenues" denominator. The LLM, lacking an explicit run-rate
ratio, invented and mislabelled a `7.6x` (which is actually RPO ÷ *quarterly* revenue).

**Fix.** Replace the single metric with two explicitly-denominated, non-redundant ratios:

| Metric name | Value | Formula string (shown to the LLM) |
| --- | --- | --- |
| `rpo_to_ttm_revenue` | RPO ÷ trailing-12-mo revenue | `RemainingPerformanceObligation / Revenues (TTM)` |
| `rpo_to_annualized_quarterly_revenue` | RPO ÷ (latest-quarter revenue × 4) | `RemainingPerformanceObligation / (latest-quarter Revenues × 4)` |

These diverge for fast growers (for a name with surging revenue the TTM base is much smaller than
the annualized latest quarter), so both carry signal; for a steady name they converge (~2x). The
misleading 7.6x quarterly ratio is intentionally **not** emitted.

**Mechanics (reuse existing helpers).**
- RPO: `_latest_fact(idx, "RemainingPerformanceObligation")` → `(period, rpo)` (unchanged).
- TTM revenue: `_ttm(idx, "Revenues")` → `(value, inputs)`.
- Latest-quarter revenue: `_fact(idx, "Revenues", _quarterly_periods(idx)[0])`
  (`_quarterly_periods` is sorted newest-first).
- Build with `_make(name, _div(rpo.value, denom), period, [_in(rpo), *rev_inputs])`.

**Soft-fail per ratio.** Emit `rpo_to_ttm_revenue` only when `_ttm(idx, "Revenues")` succeeds
(needs the quarters it requires); emit `rpo_to_annualized_quarterly_revenue` only when a latest
quarterly Revenues fact exists. If neither denominator is available, return `[]` (as today when
RPO is absent). `rpo_to_revenue` is **removed**.

**Tests.** Update `tests/.../test_metrics.py` `_backlog` cases: a dossier with RPO + 4 quarterly
+ FY revenue emits **both** ratios with correct values and explicit formula strings; a dossier
with RPO but no quarterly revenue emits only `rpo_to_ttm_revenue` (or none); assert
`rpo_to_revenue` no longer appears.

---

## 3. Part 2 — Deterministic consensus-relative stance

**Problem.** `stance` is LLM-declared against a multi-dimensional anchor (a P/E + a price target +
a rating), so "above/below expectations" is ambiguous and can contradict the scenarios.

**Fix — derive stance from the base-case return vs the consensus target upside.**

- **Enum rename** (`saturn/models.py`, `AlphaThesis.stance`):
  `above_expectations | in_line | below_expectations | unclear`
  → `above_consensus | in_line_consensus | below_consensus | unclear`.
- **Deriver** (`saturn/agents/synthesist.py`), a pure helper:
  ```python
  _STANCE_BAND = 0.10  # 10 percentage points

  def _derive_stance(base_return: float | None, target_upside: float | None) -> str | None:
      """Consensus-relative stance from Saturn's base-case return vs the Street's target upside.
      Returns None when it can't be derived (no target / no base return) — caller keeps the
      LLM-declared stance in that case."""
      if base_return is None or target_upside is None:
          return None
      if base_return >= target_upside + _STANCE_BAND:
          return "above_consensus"
      if base_return <= target_upside - _STANCE_BAND:
          return "below_consensus"
      return "in_line_consensus"
  ```
  Both values are fractions (e.g. `0.11`, `0.45`), consistent with `implied_return_pct` and
  `consensus.target_upside_pct`.
- **Wiring** in `_build_thesis`: after pricing, if `dossier.consensus.target_upside_pct` is
  present and a `base` scenario has an `implied_return_pct`, **override** the (sanitized)
  LLM-declared stance with `_derive_stance(...)`. When it returns `None` (no consensus target —
  reverse-DCF/none anchor), keep the LLM-declared stance sanitized to the new enum. Because the
  derived stance comes from the base leg, it can never contradict the scenario table.
  - MSFT: base `+0.11` vs target `+0.45` → `+0.11 ≤ 0.45 − 0.10` → **`below_consensus`**.
- **Prompt** (`SYNTHESIZE_SYSTEM` / `_synthesize_prompt`): update the allowed stance values to the
  new enum; note stance is "relative to consensus (or, without consensus, to the model-implied
  anchor)"; the LLM's declared value is a fallback used only when no consensus target exists.
- **Render** (`_render_alpha`): show the derivation for transparency, e.g.
  `**Stance:** below consensus · confidence medium  (base +11% vs consensus target +45%)`.
  When the stance was LLM-declared (no consensus target), show
  `(vs model-implied anchor; no consensus target)` instead.
- **Critic consistency check** (`critic.py` `_critic_prompt` `alpha_note`): add an instruction to
  flag (category `unsupported_alpha_inference`) when the **alpha stance contradicts the Final
  View** — e.g. stance `below_consensus` while the Final View reads as an aggressive buy. Both the
  alpha thesis and `final_view` are already in the Critic's scan.

**Tests.**
- `_derive_stance` matrix: above / in-line / below / `None` (missing target) / `None` (missing base).
- `synthesize` overrides the LLM stance with the derived one when consensus target present
  (LLM says `above_consensus`, base +11% vs +45% → result `below_consensus`); keeps the
  LLM-declared stance when no consensus.
- `_render_alpha` shows the derivation line.
- Critic flags a stance-vs-Final-View contradiction (real-ish stub finding survives).
- The completeness gate's `stance != "unclear"` check keeps working with the new enum values.

> **Migration note:** the enum rename is a breaking change to `AlphaThesis.stance`. Every
> existing test/fixture that uses the old literals (`above_expectations` / `below_expectations`
> in `test_synthesist.py`, `test_critic.py`, `test_markdown_report.py`, and `MockLLMClient`'s
> `_ALPHA` which uses `in_line`) must be updated to the new values. The plan must enumerate these
> so the rename is applied everywhere in one pass.

---

## 4. Part 3 — High-severity backstop

### (a) Warning banner (`saturn/reports/markdown_report.py`)
Immediately **after §1 Executive Summary** (before §2 Alpha Thesis), if the final
`report.critic_review` contains any **high-severity** findings, render a prominent blockquote:

```
> ⚠️ **Unresolved high-severity audit finding(s)** — treat the affected figures as provisional:
> - **[valuation_discussion]** RPO-to-annualized-revenue ratio internally inconsistent
```

- Helper `_render_high_severity_banner(review) -> list[str]`: returns `[]` when `review is None`
  or has no `severity == "high"` findings; otherwise the blockquote listing each high finding as
  `[section] claim` (claim truncated to ~120 chars). Pure function.
- "Unresolved" = present in the **final** review (i.e. whatever survives self-repair). A report
  that self-repair cleaned to zero high findings shows no banner.

### (b) Self-repair handles cross-section contradictions (`saturn/agents/critic.py` `revise`)
Today `revise` only edits the sections **named** in actionable findings. A `contradiction`'s wrong
value can live in a *different* section than the one the Critic named (the MSFT case). Fix:

- When any actionable finding is category **`contradiction`**, expand the editable/`affected` set
  to **all** analysis + debate section keys (not just the named ones), and pass all their current
  text to the revise LLM so it can reconcile whichever side holds the wrong value.
- Non-contradiction actionable findings keep the current named-section-only scope.
- Everything else is unchanged: the LLM returns corrected text only for sections it changed; the
  deterministic `model_copy` splice leaves untouched sections verbatim; and the **strict
  score-gate** (`_score(revised) < _score(original)`) still rejects any rewrite that doesn't
  reduce the severity-weighted finding score. So broadening scope cannot silently damage the
  report — a worse rewrite is discarded.

**Tests.**
- `_render_high_severity_banner`: `None`/no-high → `[]`; one high finding → blockquote containing
  the section + claim. Integration: `render` places the banner between §1 and §2 when a high
  finding exists; absent otherwise.
- `revise` contradiction path: given a `contradiction` finding naming section A, the affected set
  includes a section B (a non-named analysis/debate section) — i.e. `revise` is given B's text and
  can return a correction for B. A non-contradiction actionable finding still scopes to its named
  section only.
- Existing self-repair keep-if-better tests stay green (score-gate unchanged).

---

## 5. Files touched

- `saturn/analytics/metrics.py` — `_backlog` (Part 1)
- `saturn/models.py` — `AlphaThesis.stance` enum (Part 2)
- `saturn/agents/synthesist.py` — `_derive_stance`, `_build_thesis` wiring, prompt enum (Part 2)
- `saturn/agents/critic.py` — `alpha_note` stance-vs-final-view instruction (Part 2), `revise`
  cross-section scope (Part 3)
- `saturn/reports/markdown_report.py` — `_render_alpha` stance derivation line (Part 2),
  `_render_high_severity_banner` + placement (Part 3)
- tests for each of the above.

## 6. Non-goals / risks

- **No probability/EV column** — deliberately excluded (fabrication surface).
- **Cross-section `revise`** broadens the rewrite surface; the strict score-gate is the safety
  net (already in place). If a broadened rewrite regresses, it is discarded and the original
  (with the banner) ships.
- **Stance band (10pp)** is a chosen constant; documented inline so it's tunable. It is applied
  only to the deterministic consensus path — the reverse-DCF/no-consensus path is unaffected.

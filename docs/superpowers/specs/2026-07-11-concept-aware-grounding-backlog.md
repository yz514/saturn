# Backlog: Concept-Aware Grounding for the Critic

**Date:** 2026-07-11
**Status:** Backlog (not scheduled) — captured while building Critic-v2
**Relates to:** `saturn/agents/critic.py` (`is_number_grounded`), roadmap follow-up F1 (hallucination/grounding), Phase 5 (Evaluation)

## Problem

The Critic's deterministic numeric backstop (`is_number_grounded`) is **concept-agnostic**.
It decides a claimed number is "grounded" if that magnitude matches *any* value in the
dossier within ±2% (or, for figures quoted verbatim, appears in the filing text). It does
not know **what the number refers to**.

This produces **false negatives in error detection**: a wrong figure gets silently dropped
because its magnitude collides with an unrelated datum.

### Demonstrated case (MU, 2026-07-11)

A fabricated bear thesis — *"Micron's revenue fell to just $2 billion, a sign of terminal
decline"* — is a blatant error (MU revenue is ~$37B/FY, ~$41.5B/quarter). The real LLM Critic
**correctly flags it** as `unsupported_number`. But the backstop then **drops it**, because
the dossier contains unrelated ~$2B line items:

- `OperatingCashFlow` Q1 FY2020 = $2.011B
- `DepreciationAndAmortization` Q1 FY2025 = $2.030B

"$2 billion revenue" grounds against `OperatingCashFlow`/`D&A` purely on magnitude. The
backstop cannot tell "revenue = $2B" (wrong) from "OCF = $2B" (right).

> Note: this is **distinct** from the single-digit substring bug fixed in the Critic-v2
> branch (a bare `"2"` matching any digit in filing text). That fix is real and shipped; it
> just doesn't address magnitude collision, which lives in the value-match (`dvals`) path.

## Why not a quick heuristic

Tightening the `dvals` path (e.g. "reject matches for <2 significant-digit magnitudes")
would fix *this* sample but reintroduce noise: coarse-but-correct claims ("~$2B in capex")
would no longer be auto-dropped, so any the LLM over-flags would surface as findings. That is
exactly the **overfit-on-one-sample** failure mode we've hit before (tuning LLM-output filters
on a single run — they don't generalize). One observed case is not enough signal to justify a
new magnitude rule.

## Proposed direction (when scheduled)

Make grounding **concept-aware**: associate the claim's number with the *subject* it describes,
then match against the fact for *that* concept, not any fact.

Sketch:
1. **Extract (subject, number) pairs** from the claim, not bare numbers — e.g. from
   "revenue fell to $2B" derive `subject≈revenue`, `value≈2e9`. A light dependency/keyword
   parse or a small structured LLM step ("what quantity does each figure describe?").
2. **Resolve subject → canonical concept(s)** via a synonym map over `EDGAR_CONCEPTS`
   (revenue → `Revenues`/`RevenueFromContractWithCustomerExcludingAssessedTax`; FCF, margin,
   OCF, D&A, EPS, …). Reuse the alias infrastructure already in `edgar.py`.
3. **Ground against the matched concept only.** "$2B revenue" checks `Revenues` (~$37B) →
   no match → **not grounded** → the finding stands. "$18.3B FCF" checks the FCF metric →
   match → grounded.
4. **Fallback** to the current magnitude/source check only when no subject can be resolved,
   so behavior degrades gracefully rather than regressing.

## Scope & cost

- Touches only `saturn/agents/critic.py` (grounding) plus a small concept-synonym map
  (possibly shared with `edgar.py`). No workflow/model changes.
- Adds either a cheap parse or one small LLM call per critique; keep it deterministic where
  possible.
- **Validate against a real LLM on several tickers** before trusting it (standing lesson);
  build an eval set of known-good and known-wrong (subject, number) claims so we measure
  recall/noise rather than eyeballing one report.

## Acceptance signal

On a labeled set: "revenue = $2B" (wrong) is **kept**; "$18.3B FCF", "26.1% operating
margin", "$5.0B RPO" (right) are **dropped as grounded** — with no material rise in
false-positive findings vs. the current backstop.

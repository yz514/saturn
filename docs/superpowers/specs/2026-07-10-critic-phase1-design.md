# The Critic (Phase 1, advisory) — Design

**Date:** 2026-07-10
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Add a **verification agent** that reads the drafted report against the provenance-tagged
dossier and surfaces where the analyst's prose diverges from the data — the **F1
anti-hallucination** goal. Advisory (non-blocking): it annotates, it does not rewrite or
block. More needed now that the analyst *transcribes* numbers from press-release text.

Motivating bugs it must catch (test cases from our own runs):
- **Contradiction:** Open Questions asked if the Mar/Apr 8-Ks were "trade/regulatory"
  while §15 showed debt tenders; "Cloud fastest-growing" vs the table's Core DC +653%.
- **Unsupported number:** a figure in prose not traceable to any dossier datum/source.
- **Over-weighting:** leading the thesis with the reverse-DCF fair value when it's flagged
  low-confidence.

## 2. Scope (v1)

Three checks: **(1) numeric grounding**, **(2) internal contradictions**, **(3)
over-weighting a low-confidence signal**. (Completeness / "what's missing" → later; a
perspective-diverse panel → v2.) It reviews the **analytical prose** (`AnalysisSections`
+ `DebateSections`); the data tables are the ground truth it checks against.

## 3. Architecture

New agent module **`saturn/agents/critic.py`** (the first real agent module). Workflow
becomes `analyze → debate → **critique** → assemble → render`. Advisory + **soft-fail**:
any critic error (LLM/JSON) returns `None` → no Verification section, report unaffected.

### Hybrid method (the verifier must itself be checkable)
- **LLM critic** → forced *structured* findings. Given the drafted prose + the same
  `_company_context(dossier)` the analyst saw + the reverse-DCF low-confidence verdict.
  Prompt: only report where prose isn't supported by the data; quote exactly; don't
  invent issues; if well-supported, say nothing.
- **Deterministic grounding backstop** (pure Python) → `is_dollar_grounded(token,
  dossier)`: parse a `$X.XB` / `$X,XXX` / `XXB` dollar token to a float and return True
  if it matches a dossier fact/metric value within a relative tolerance **or** its
  normalized digits appear in the ingested filing/press-release source text. Used to
  **filter the LLM's `unsupported_number` findings** — drop any whose dollar figure is
  actually grounded (removes critic false positives). *Nuance:* legitimately **derived**
  numbers (YoY %, annualized FCF) won't match raw data and are the LLM's judgment, not
  the deterministic layer's — so the backstop only *removes* false alarms, never *adds*
  findings.

## 4. Data model (`saturn/models.py`)

```python
class CriticFinding(BaseModel):
    claim: str        # exact quote from the report
    section: str      # e.g. "executive_summary", "bear_thesis"
    category: str     # "unsupported_number" | "contradiction" | "over_weighting" | "unverified_claim"
    verdict: str      # "contradicted" | "unsupported" | "flagged"
    evidence: str     # the dossier datum, or the conflicting statement
    severity: str     # "high" | "medium" | "low"

class CriticReview(BaseModel):
    findings: list[CriticFinding] = Field(default_factory=list)
    claims_checked: int = 0
    summary: str = ""
    provenance: Provenance          # source="Saturn (critic)"
```
`ResearchReport` gains `critic_review: CriticReview | None = None`.

## 5. Rendering

A new **`## Verification (Critic)`** section (placed after Final View, before Macro), via
`markdown_report.render`:
- one line summary + `claims_checked`,
- each finding as `⚠️ **{category}** [{section}, {severity}]: "{claim}" — {evidence}`,
- when `critic_review is None`: `_Verification unavailable._`; when `findings == []`:
  `_No material discrepancies found against the underlying data._`.
Section numbers shift by one below it (tests assert the new numbering).

## 6. Workflow integration

In `run()`: after `debate`, call `critique(analysis, deb, company, llm, model=call_model)`
and pass the result to `ResearchReport(..., critic_review=review)`. `critique` builds its
prompt from a lazily-imported `_company_context` (avoids a circular import with
`equity_research`) + the two section objects; parses the LLM JSON to `CriticReview`
(soft-fail to `None`); applies the grounding backstop.

## 7. Testing

- **Grounding helper:** `is_dollar_grounded("$90.3B", dossier)` True when a fact/metric ≈
  90.3e9; `"$18.3B"` True when it's in the ingested source text; `"$999B"` False.
- **`critique` with a mock LLM** returning a canned findings JSON → parses to
  `CriticReview`; a malformed/raising LLM → returns `None` (soft-fail).
- **Backstop filter:** an `unsupported_number` finding whose figure IS grounded is dropped;
  an ungrounded one is kept.
- **Render:** findings → Verification section; `None` → "unavailable"; `[]` → "no
  discrepancies"; section numbering below it updated.
- **Manual live check:** run MU; confirm the Critic flags the known bugs (a
  Cloud-vs-Core contradiction if present; the reverse-DCF over-weighting) and doesn't
  flag well-grounded headline figures.

## 8. Cost / edges

One extra LLM call per report (acceptable; skipped in `--mock` via the mock client, which
returns a canned review). Non-deterministic LLM output → the deterministic backstop and
the advisory (non-blocking) stance bound the risk. The Critic reads prose only; it never
edits the report.

## 9. Out of scope

- Revise-loop / gate (chosen: advisory).
- Completeness / missing-data check; perspective-diverse critic panel (v2).
- Authoring scenario tables / SCA chapters (generator features, separate track).

# Alpha-Framing Layer — Design

**Date:** 2026-07-11
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user (+ external GPT review)

## 1. Goal

Move Saturn from a **fundamental memo** ("what is this company?") to an **alpha memo**
("where is the market wrong, why do we think so, what decides it, what breaks the view, and
what price range does each outcome imply?"). Sector-agnostic — MSFT, JNJ, MU, anything.

The layer forces every report to answer five questions explicitly:
1. What does the market expect? (the **anchor**)
2. Where is Saturn's view different? (the **variant / stance**)
3. Which variable decides who's right? (the **key variable**)
4. What observation would prove us wrong? (the **falsifier**)
5. What price range does each scenario imply? (the **scenario table**)

**Scope boundary:** this layer delivers *framing + a directional variant view*. It does NOT
build a rigorous independent forecast — that is the later **driver-model** stage. Here the LLM
supplies assumptions and a directional stance; deterministic code computes prices; the Critic
audits the reasoning.

## 2. Pipeline flow

```
analyze → debate → SYNTHESIZE(alpha) → critique(+audits alpha) → [self-repair] → render
```

A new **synthesist** agent (`saturn/agents/synthesist.py`) runs after `debate`, consumes
`analysis + debate + the expectation anchor`, and emits a structured `AlphaThesis`. It is the
roadmap's PM/Synthesis role — kept separate from the analyst (facts) and the critic (audit)
because its job is distinct: **turn facts into an investment judgment.** Cost: +1 LLM call per
report (the mock path returns a minimal valid thesis, so `--mock` still renders the section).

## 3. Data model (`saturn/models.py`)

```python
class ExpectationAnchor(BaseModel):
    """What the market is pricing in — the foundation the variant view is measured against."""
    source: Literal["consensus", "reverse_dcf_implied", "none"]
    metric: str | None = None      # "Forward P/E", "forward EPS", "implied FCF growth"
    period: str | None = None      # "NTM", "FY2027"
    value: float | None = None     # 6.5
    unit: str | None = None        # "x", "USD/share", "%"
    text: str                      # human-readable sentence (always present)
    confidence: Literal["high", "medium", "low"]

class ScenarioLeg(BaseModel):
    name: Literal["bull", "base", "bear"]
    period: str                    # "FY2027" | "NTM" | "cycle-normalized" — REQUIRED
    driver: str                    # the assumption in words
    metric: Literal["EPS", "FCF/share", "sales/share"]        # per-share only in v1
    metric_basis: Literal["GAAP", "non_GAAP", "adjusted", "cycle_normalized"]
    per_share_value: float
    multiple: float
    multiple_basis: Literal["P/E", "P/FCF", "P/S"]
    implied_price: float | None = None        # computed = per_share_value × multiple
    implied_return_pct: float | None = None   # computed = implied_price / quote − 1

class AlphaThesis(BaseModel):
    anchor: ExpectationAnchor
    stance: Literal["above_expectations", "in_line", "below_expectations", "unclear"]
    variant: str                   # the differentiated view, ONE sharp sentence (≤35 words)
    rationale: str                 # why, tied to the data
    confidence: Literal["high", "medium", "low"]
    key_variable: str              # the KPI that decides it
    falsifier: str                 # observable event that would break the thesis
    horizon: str                   # e.g. "next 1–2 quarters", "12–18 months"
    scenarios: list[ScenarioLeg]
    incompleteness: list[str] = Field(default_factory=list)  # filled by the gate
    provenance: Provenance

# ResearchReport gains:
    alpha_thesis: AlphaThesis | None = None
```

Field-name notes:
- `stance` is **relative to the anchor** (which may be consensus OR reverse-DCF implied), so it
  reads `above_expectations`, not `above_consensus`.
- `multiple_basis` intentionally overlaps with `metric` so the Critic can flag a mismatch
  (e.g. an `EPS` value paired with a `P/S` multiple).

## 4. Expectation anchor — `_resolve_anchor(dossier) -> ExpectationAnchor`

Deterministic resolver, preference order:
1. **`dossier.consensus`** present with a usable field (forward_pe / forward_eps / target_mean)
   → `source="consensus"`; populate metric/period/value/unit from the strongest available
   field; `text` summarises forward P/E, mean target + upside, rating, n_analysts;
   `confidence` from data richness (e.g. `medium` default, `low` if most fields rejected).
2. else **reverse-DCF implied growth** from the forward model
   (`derived_metrics` with source `"Saturn (model)"`, `implied_fcf_growth`) →
   `source="reverse_dcf_implied"`; `text` = "price implies ~G% FCF growth"; if
   `is_reverse_dcf_low_confidence(...)`, `confidence="low"` and the text carries the caveat.
3. else `source="none"`, `text="No market-expectation anchor available this run."`,
   `confidence="low"`.

## 5. Synthesist agent — `saturn/agents/synthesist.py`

`synthesize(analysis, debate, dossier, llm, *, model=None) -> AlphaThesis | None`

- Build the anchor via `_resolve_anchor`.
- `SYNTHESIZE_SYSTEM` prompt instructs the model to:
  - State the stance **relative to the anchor** and justify it with specific data from the
    analysis/debate. If it cannot take a differentiated view, return `stance="unclear"`
    honestly rather than manufacture one.
  - Keep `variant` to **one sentence ≤35 words**.
  - Name the single `key_variable`, an **observable** `falsifier` (event + time window), and a
    `horizon`.
  - Give exactly three scenarios (bull/base/bear), each with `period`, `driver`, a **per-share**
    metric + value + `metric_basis`, and a `multiple` + `multiple_basis`. Do NOT output prices
    — the system computes them.
- Resilient parse mirroring `critique`: per-leg validation, one retry on malformed JSON,
  soft-fail to `None`. `OUTPUT_SCHEMA=alpha` marker in the prompt.
- After parse, run the deterministic pricing pass (§6) and the completeness gate (§7).

## 6. Scenario pricing — deterministic (`_price_scenarios(legs, quote_price)`)

Pure function. For each leg: `implied_price = per_share_value × multiple`;
`implied_return_pct = implied_price / quote_price − 1` when `quote_price` is a positive number,
else `None` (price still shown). The **LLM never emits a price** — every target shows its math,
so the Critic can ground-check it. v1 handles **per-share metrics only** (EPS / FCF-per-share /
sales-per-share); EV/EBITDA and other enterprise metrics are out of scope (§10).

## 7. Completeness gate — deterministic (`alpha_completeness(thesis) -> list[str]`)

Checks **structural presence only** (semantic quality is the Critic's job, §8). Returns a list
of human-readable gaps; the synthesist stores it in `thesis.incompleteness`. Flags:
- anchor `source == "none"`;
- `stance != "unclear"` but `rationale` blank;
- `variant` blank or clearly bloated (> 50 words — a generous ceiling above the 35-word target);
- `key_variable`, `falsifier`, or `horizon` blank;
- fewer than 3 scenarios, or any scenario missing `period` / `per_share_value` / `multiple`.

When non-empty, the rendered section is labelled **Incomplete — low confidence** (§9) and a
Critic finding `alpha_incomplete` (severity medium) is added, so an incomplete thesis can never
render as if complete.

## 8. Critic integration (L4 / L5)

- **L5 alpha-sufficiency = the deterministic gate above** — reliable, no LLM-filter overfit.
- **L4 = extend the existing `critique` pass.** The alpha thesis text is added to what the
  Critic already scans, enabling two audits:
  - existing `contradiction` detection now covers **stance vs. bull/bear/final** and
    **scenario numbers vs. the financial snapshot**;
  - a new category **`unsupported_alpha_inference`** flags reasoning stronger than evidence:
    variant not connected to the anchor, a scenario `driver` with no data support, a
    non-observable falsifier, or an accounting inference lacking filing support (the
    "84.6% margin likely reflects upfront recognition" failure mode).

`unsupported_alpha_inference` is an **LLM-judgment instruction, not a regex**, kept advisory.
Per our standing lesson, it will be **validated against a real (non-mock) LLM** before we trust
it — never tuned on a single sample.

**v1 does NOT auto-repair the alpha thesis.** The Critic audits and flags; the self-repair loop
(which rewrites analysis/debate sections) is not extended to the structured thesis, because
revising it while keeping scenario math and cross-field consistency intact is a separate problem.
Path: v1 generate → deterministic math → critic flags → render caveat; auto-repair is v2+.

## 9. Rendering (`saturn/reports/markdown_report.py`)

New **`## 2. Alpha Thesis`** immediately after the Executive Summary; subsequent sections
renumber. Layout:
- Header: `## 2. Alpha Thesis` — appended `(Incomplete — low confidence)` when the gate flagged
  anything.
- **Anchor** line (with `source` tag; carries the reverse-DCF low-confidence note when implied).
- **Stance** + confidence.
- **Variant perception** (the one-liner) and **Rationale**.
- **Key variable / Falsifier / Horizon**.
- **Scenario table:** `Scenario | Period | Driver | Math | Price | Return`, where Math renders
  `per_share_value {metric} × multiple {multiple_basis}` and Price/Return come from §6.
- If `thesis is None`: `_Alpha thesis unavailable this run._`

## 10. Error handling & edges

- `synthesize` soft-fails to `None` (never breaks the report).
- `anchor.source == "none"` still produces a (qualitative) thesis; the gate records the missing
  anchor and the section is labelled incomplete.
- No quote price → returns are `None`, prices still render.
- Mock path: `MockLLMClient` returns a minimal valid `OUTPUT_SCHEMA=alpha` JSON so the section
  renders under `--mock`.

## 11. Testing

**Deterministic units**
- `_resolve_anchor`: consensus present → `consensus` anchor with metric/value; consensus
  absent + forward model present → `reverse_dcf_implied` (+ low-conf when flagged); neither →
  `none`.
- `_price_scenarios`: `EPS × multiple` → correct price and return; no/zero quote → `None` return.
- `alpha_completeness`: complete thesis → `[]`; each gap (missing anchor, blank falsifier,
  <3 scenarios, scenario missing `period`, bloated variant) → flagged.
- `synthesize`: mock LLM returns valid alpha JSON → `AlphaThesis` with computed prices;
  malformed → `None`; one bad leg dropped, rest kept.

**Critic (real-ish stub) — the review's five, mapped**
- `test_scenario_period_required` → gate flags a scenario missing `period`.
- `test_falsifier_observable` → gate flags a blank/vacuous falsifier or missing horizon.
- `test_alpha_variant_must_reference_anchor` → Critic `unsupported_alpha_inference` when the
  variant doesn't connect to the anchor.
- `test_unsupported_accounting_inference_flagged` → Critic flags an accounting inference with no
  contract-liability / deferred-revenue / filing support.
- `test_final_view_matches_alpha_stance` → Critic flags stance vs. final-view contradiction.

**Integration**
- `run()` populates `alpha_thesis`; `render` shows the §2 section, scenario table, falsifier,
  and the Incomplete label when applicable.

**Live (post-implementation)**
- Regenerate a real report (MU or another) and confirm: §2 states an anchor, a one-line variant
  vs. that anchor, a key variable, an observable falsifier, and a scenario table whose prices
  equal value×multiple. Then verify `unsupported_alpha_inference` against the real LLM on a
  deliberately over-reaching draft.

## 12. Scope

**In:** `ExpectationAnchor` / `ScenarioLeg` / `AlphaThesis` models + `ResearchReport.alpha_thesis`;
`synthesist.py` (`synthesize`, `SYNTHESIZE_SYSTEM`, `_resolve_anchor`, `_price_scenarios`,
`alpha_completeness`); Critic extension (alpha text into the scan + `unsupported_alpha_inference`);
rendering (§2 + renumber); workflow wiring in `run()`; `MockLLMClient` alpha branch; tests.

## 13. Out of scope (later stages)

- Committed independent forecast / driver model (the Stage-B "build your own number").
- EV/EBITDA and other enterprise-value scenario math (per-share only in v1).
- Auto-repair of the alpha thesis (v2+).
- Numeric-threshold falsifiers + persistent thesis tracking (Stage F).
- Segment/KPI-level consensus estimates.

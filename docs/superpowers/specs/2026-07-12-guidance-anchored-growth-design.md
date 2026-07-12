# Guidance-Anchored Growth (Driver Model Slice 1.5) — Design

**Date:** 2026-07-12
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** Driver Model Slice 1 (PR #32, branch `driver-model`). This branch
(`guidance-anchored-growth`) is stacked on it; merge #32 first, then this.

## 1. Goal & honest framing

Slice 1's driver model anchors the base case on a **trailing-trend** revenue growth (3-yr CAGR) —
explicitly a backward-looking baseline. This slice makes the growth input **forward-looking and
grounded**: when management has disclosed forward revenue **guidance** (in the earnings-release /
8-K exhibits Saturn already ingests), use that guidance as the growth input instead of the trend.

**Honesty guardrail:** this is an LLM **extraction of a stated figure**, not LLM forecasting.
The extracted guidance is accepted only if its cited sentence is **found verbatim in the ingested
filing text** — otherwise Saturn falls back to the trailing-trend baseline. So Saturn's number
becomes "revenue at management's own guided growth × trailing margin," fully traceable to a quote.

## 2. Flow

```
dossier (trend driver model) → EXTRACT_GUIDANCE (LLM) → [if grounded] recompute driver model with
guidance growth → analyze → debate → synthesize → critique → render
```

Guidance extraction is a **workflow-time step** in `run()` (keeps ingestion pure/offline), run
BEFORE `analyze` so every agent sees the guidance-anchored model. Cost: **+1 LLM call per report**;
mock path returns no guidance → trend fallback (no behavior change under `--mock`).

## 3. `extract_guidance(dossier, llm, *, model=None) -> Guidance | None`

New `saturn/agents/guidance.py`. Prompts over the earnings-release / 8-K `filing_sections` and asks
for management's forward **revenue** guidance as JSON: `value` (the guided revenue figure, same
scale as reported Revenues), `period` (`"FY"` or `"quarter"`), and `quote` (the verbatim sentence).
Resilient parse (one retry, like `critique`), soft-fail to `None` when absent or unparseable.

After the LLM call, deterministically:
1. **Ground the quote:** normalise whitespace and require `quote` to be a substring of the
   concatenated `filing_sections` excerpts. If not found → return `None` (discard — this is the
   anti-hallucination gate; a fabricated guide won't match the source).
2. **Compute `implied_growth`** vs TTM revenue (`_ttm_or_fy(idx, "Revenues")`): FY guide →
   `value / TTM_revenue − 1`; quarter guide → `(value × 4) / TTM_revenue − 1` (annualised, with a
   caveat noting the annualisation).
3. Return a `Guidance` with `metric="revenue"`, `value`, `period`, `quote`, `implied_growth`,
   provenance `source="SEC EDGAR (guidance)"`.

Sanity bound: if `implied_growth` is absurd (e.g. `< -0.9` or `> 2.0`), discard (→ trend). This
catches a mis-scaled figure (quarter mistaken for FY, etc.).

## 4. Data model

New `class Guidance(BaseModel)` in `saturn/models.py`:
```python
class Guidance(BaseModel):
    metric: str = "revenue"
    period: str                      # "FY" | "quarter"
    value: float                     # guided revenue figure (absolute, Revenues scale)
    implied_growth: float            # computed vs TTM revenue
    quote: str                       # verbatim sentence from the filing
    provenance: Provenance
```

`DriverModel` gains two fields:
```python
    growth_source: str = "trend"     # "trend" | "guidance"
    growth_citation: str = ""        # the verbatim guidance quote when source == "guidance"
```

## 5. Feeding the bridge — `compute_driver_model(..., *, growth_override=None)`

`saturn/analytics/driver.py`'s `compute_driver_model` gains a keyword-only `growth_override:
float | None = None`:
- When `growth_override is not None`: use it as `g` (skip the 3-yr CAGR), set
  `growth_source="guidance"`, and do NOT add the "no 3-year history" low-confidence caveat (a
  guided growth is a legitimate forward input even without trailing history).
- When `None`: unchanged Slice-1 behaviour (`growth_source="trend"`).
- `growth_citation` defaults `""`; the workflow stamps it from the guidance quote after recompute
  (keeps `driver.py` free of guidance-object coupling).

## 6. Wire in `run()` (`saturn/workflows/equity_research.py`)

Before `analyze`:
```python
guidance = extract_guidance(company, llm, model=call_model)
if guidance is not None:
    company.driver_model = compute_driver_model(
        company.fundamentals, company.quote, company.consensus,
        growth_override=guidance.implied_growth,
    )
    if company.driver_model is not None:
        company.driver_model.growth_citation = guidance.quote
```
Soft-fail: `extract_guidance` returning `None` (no guidance / ungrounded / absurd) leaves the
Slice-1 trend model intact. All downstream (`_company_context`, render) already reads
`company.driver_model`.

## 7. Render / context

- **Driver Bridge (§2 subsection):** the growth figure is annotated by source — `rev growth
  +15.0% (per management guidance)` when `growth_source == "guidance"`, else `(trailing trend)`.
  When guidance-sourced, append a line with the citation: `_Guidance: "<quote>"_`.
- **`_company_context`:** the DRIVER MODEL block notes the growth source and includes the guidance
  quote so `analyze`/`debate`/`synthesize` can cite management's own number.
- Stance derivation, scenarios, and anchor remain untouched.

## 8. Testing

- **`extract_guidance`:** mock LLM returns a guidance JSON whose quote IS in the dossier's filing
  text → returns a `Guidance` with correct `implied_growth` (FY and quarter cases); quote NOT in
  the filing text → `None` (grounding gate); malformed JSON → `None`; absurd implied growth →
  `None`.
- **`compute_driver_model` override:** `growth_override=0.15` → `trailing_revenue_growth==0.15`,
  `growth_source=="guidance"`, no "no 3-year history" caveat even when history is absent; without
  override → `growth_source=="trend"` (Slice-1 behaviour unchanged).
- **`run()` wiring:** a stateful stub (guidance extracted + grounded) → the report's
  `driver_model.growth_source=="guidance"` and `growth_citation` set; no-guidance stub → trend
  model unchanged; existing driver/self-repair tests stay green.
- **Render/context:** Driver Bridge shows "(per management guidance)" + the quote when guided;
  "(trailing trend)" otherwise; `_company_context` includes the guidance source.
- **Live:** regenerate a report for a name that guides revenue (e.g. MSFT/MU) and confirm the
  Driver Bridge uses guidance growth with a real cited quote, or cleanly falls back to trend.

## 9. Scope

- **Modify/create:** `saturn/agents/guidance.py` (new), `saturn/models.py` (`Guidance` +
  `DriverModel.growth_source`/`growth_citation`), `saturn/analytics/driver.py` (`growth_override`),
  `saturn/workflows/equity_research.py` (extract + recompute wiring + `_company_context` note),
  `saturn/reports/markdown_report.py` (Driver Bridge source annotation + citation),
  `saturn/llm/mock_client.py` (a guidance branch so `--mock` is deterministic); touched tests.

## 10. Out of scope

- Using EPS guidance directly (bypassing the revenue×margin bridge); margin guidance.
- Per-segment guidance; multi-year guidance ramps.
- Critic verification of guidance beyond the deterministic quote-grounding gate.
- Consensus-revenue ingestion / clean growth-vs-margin waterfall (a separate later slice).

# Alpha-Frame v1.1 Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the alpha frame trustworthy and unambiguous: explicit RPO coverage ratios, a deterministic consensus-relative stance, and a high-severity backstop (warning banner + cross-section self-repair).

**Architecture:** Three independent parts. Part 1 is data-layer (metric catalog + `_backlog`). Part 2 replaces the LLM-declared `stance` with one derived deterministically from the base-case return vs consensus target. Part 3 adds a render banner for unresolved high findings and lets `revise` edit all sections for `contradiction` findings.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-alpha-frame-v1.1-hardening-design.md`.

**File map:**
- `saturn/analytics/catalog.py` + `saturn/analytics/metrics.py` — RPO ratios (Task 1)
- `saturn/models.py` + `saturn/agents/synthesist.py` + `saturn/llm/mock_client.py` — stance enum + derivation (Task 2)
- `saturn/reports/markdown_report.py` + `saturn/agents/critic.py` — stance render + Critic consistency (Task 3)
- `saturn/reports/markdown_report.py` — high-severity banner (Task 4)
- `saturn/agents/critic.py` — `revise` cross-section (Task 5)

---

### Task 1: RPO coverage ratios

**Files:**
- Modify: `saturn/analytics/catalog.py`
- Modify: `saturn/analytics/metrics.py` (`_backlog`)
- Test: `tests/analytics/test_metrics.py`, `tests/analytics/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `test_rpo_to_revenue_uses_latest_rpo_over_latest_fy_revenue` in `tests/analytics/test_metrics.py` with:

```python
def test_backlog_emits_two_explicit_rpo_ratios():
    # Full 4 quarters -> TTM revenue = 92; latest quarter (Q4) = 26 -> annualized run-rate = 104.
    rows = [
        ("RemainingPerformanceObligation", "Q4 FY2026", 100.0),
        ("Revenues", "Q1 FY2026", 20.0), ("Revenues", "Q2 FY2026", 22.0),
        ("Revenues", "Q3 FY2026", 24.0), ("Revenues", "Q4 FY2026", 26.0),
    ]
    ms = compute_metrics(_facts(rows), None)
    ttm = _by_name(ms, "rpo_to_ttm_revenue", "Q4 FY2026")
    annq = _by_name(ms, "rpo_to_annualized_quarterly_revenue", "Q4 FY2026")
    assert ttm is not None and abs(ttm.value - 100 / 92) < 1e-9
    assert annq is not None and abs(annq.value - 100 / 104) < 1e-9
    assert _by_name(ms, "rpo_to_revenue", "Q4 FY2026") is None    # old ambiguous metric removed


def test_backlog_soft_fails_without_quarterly_revenue():
    # RPO but only annual revenue -> the run-rate ratios need quarterly data -> none emitted.
    rows = [("RemainingPerformanceObligation", "Q3 FY2026", 5.0), ("Revenues", "FY2025", 40.0)]
    ms = compute_metrics(_facts(rows), None)
    assert _by_name(ms, "rpo_to_ttm_revenue", "Q3 FY2026") is None
    assert _by_name(ms, "rpo_to_annualized_quarterly_revenue", "Q3 FY2026") is None
```

In `tests/analytics/test_catalog.py`, update the `CANONICAL` set: replace `"rpo_to_revenue"` on the `days_sales_outstanding` line with `"rpo_to_ttm_revenue", "rpo_to_annualized_quarterly_revenue"`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py tests/analytics/test_catalog.py -q`
Expected: FAIL (`rpo_to_ttm_revenue` not in catalog → KeyError in `_make`, or metric missing).

- [ ] **Step 3: Update the catalog**

In `saturn/analytics/catalog.py`, in the `_DEFS` list, **remove** the `rpo_to_revenue` line and **add** these two in its place (in the `# Efficiency` group, after `days_sales_outstanding`):

```python
    _d("rpo_to_ttm_revenue", "Efficiency", "x", "RemainingPerformanceObligation / Revenues (TTM)", "Contracted backlog (RPO) as a multiple of trailing-twelve-month revenue — revenue visibility.", "GAAP RPO excludes non-binding long-term supply commitments (e.g. SCA minimums)."),
    _d("rpo_to_annualized_quarterly_revenue", "Efficiency", "x", "RemainingPerformanceObligation / (latest-quarter Revenues x 4)", "Contracted backlog (RPO) vs the latest quarter's annualized run-rate revenue — coverage vs current run-rate.", "GAAP RPO excludes non-binding long-term supply commitments; annualization assumes the latest quarter is representative."),
```

- [ ] **Step 4: Rewrite `_backlog`**

In `saturn/analytics/metrics.py`, replace the whole `_backlog` function with:

```python
def _backlog(idx) -> list[DerivedMetric | None]:
    """RPO coverage over two explicit revenue bases (explicit denominators avoid the ambiguity
    that let an LLM fabricate a mislabelled ratio). rpo_to_ttm_revenue uses trailing-12-mo
    revenue; rpo_to_annualized_quarterly_revenue uses the latest quarter annualized (current
    run-rate). Both need quarterly revenue, so annual-only filers emit neither."""
    latest = _latest_fact(idx, "RemainingPerformanceObligation")
    if not latest:
        return []
    period, rpo = latest
    out: list[DerivedMetric | None] = []
    ttm = _ttm(idx, "Revenues")
    if ttm is not None:
        ttm_val, ttm_inputs = ttm
        out.append(_make("rpo_to_ttm_revenue", _div(rpo.value, ttm_val), period, [_in(rpo), *ttm_inputs]))
    qps = _quarterly_periods(idx)
    if qps:
        rev_q = _fact(idx, "Revenues", qps[0])
        if rev_q is not None and rev_q.value:
            out.append(_make("rpo_to_annualized_quarterly_revenue",
                             _div(rpo.value, rev_q.value * 4), period, [_in(rpo), _in(rev_q)]))
    return out
```

- [ ] **Step 5: Run to verify they pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py tests/analytics/test_catalog.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green. (If any other test or the metrics-reference doc references `rpo_to_revenue`, update it to the new names.)

- [ ] **Step 6: Commit**

```bash
git add saturn/analytics/catalog.py saturn/analytics/metrics.py tests/analytics/test_metrics.py tests/analytics/test_catalog.py
git commit -m "feat(metrics): explicit RPO coverage ratios (ttm + annualized-quarterly), drop ambiguous rpo_to_revenue"
```
Commit trailer (all commits): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: Consensus-relative stance enum + deterministic derivation

**Files:**
- Modify: `saturn/models.py`, `saturn/agents/synthesist.py`, `saturn/llm/mock_client.py`
- Test: `tests/agents/test_synthesist.py` (+ migrate old-enum literals in `test_critic.py`, `test_markdown_report.py`)

- [ ] **Step 1: Write the failing tests** (append to `tests/agents/test_synthesist.py`)

```python
def test_derive_stance_matrix():
    from saturn.agents.synthesist import _derive_stance
    assert _derive_stance(0.60, 0.45) == "above_consensus"     # base well above target
    assert _derive_stance(0.11, 0.45) == "below_consensus"     # base well below target (MSFT)
    assert _derive_stance(0.42, 0.45) == "in_line_consensus"   # within the 10pp band
    assert _derive_stance(0.11, None) is None                  # no target -> keep LLM stance
    assert _derive_stance(None, 0.45) is None                  # no base return


def test_synthesize_overrides_stance_from_consensus_target():
    from saturn.models import ConsensusSnapshot
    # base leg 10x15=150 vs quote 100 -> +50%; consensus target +80% -> +50% <= +70% -> below_consensus,
    # overriding the LLM's declared "above_consensus".
    d = _dossier(quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
                 consensus=ConsensusSnapshot(target_mean=180.0, target_upside_pct=0.80,
                                             provenance=Provenance(source="yfinance (estimate)")))
    t = synthesize(_analysis(), _debate(), d, _AlphaLLM(_valid_alpha_json()))
    assert t.stance == "below_consensus"
    assert "base +50% vs consensus target +80%" in t.stance_basis


def test_synthesize_keeps_llm_stance_without_consensus_target():
    # consensus present but no target_upside_pct -> derive returns None -> keep LLM's stance.
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_valid_alpha_json()))
    assert t.stance == "above_consensus"                       # from the (updated) payload
    assert "no consensus target" in t.stance_basis
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k "stance"`
Expected: FAIL (`_derive_stance` missing; `stance_basis` missing; payload still uses old literal).

- [ ] **Step 3: Rename the enum + add `stance_basis` (models.py)**

In `saturn/models.py`, in `class AlphaThesis`, change the `stance` line and add `stance_basis`:

```python
    stance: Literal["above_consensus", "in_line_consensus", "below_consensus", "unclear"] = "unclear"
    stance_basis: str = ""   # human note on how stance was derived (or that it was LLM-declared)
```

- [ ] **Step 4: Add `_derive_stance` + wire into `_build_thesis` (synthesist.py)**

At the top of `saturn/agents/synthesist.py` (near `_MAX_OUTPUT_TOKENS`), add:

```python
_STANCE_BAND = 0.10  # ±10 percentage points around the consensus target defines "in line"


def _derive_stance(base_return: float | None, target_upside: float | None) -> str | None:
    """Consensus-relative stance from Saturn's base-case return vs the Street's target upside.
    Returns None when it can't be derived (no target / no base return) so the caller keeps the
    LLM-declared stance. Because it reads the base leg, it can never contradict the scenarios."""
    if base_return is None or target_upside is None:
        return None
    if base_return >= target_upside + _STANCE_BAND:
        return "above_consensus"
    if base_return <= target_upside - _STANCE_BAND:
        return "below_consensus"
    return "in_line_consensus"
```

In `_build_thesis`, replace the `stance=_one_of(...)` construction. Current code:

```python
    thesis = AlphaThesis(
        anchor=anchor,
        stance=_one_of(
            data.get("stance"),
            ("above_expectations", "in_line", "below_expectations", "unclear"),
            "unclear",
        ),
        variant=str(data.get("variant") or ""),
        ...
```

Change to (compute stance + basis BEFORE constructing the thesis, then pass them in):

```python
    stance = _one_of(
        data.get("stance"),
        ("above_consensus", "in_line_consensus", "below_consensus", "unclear"),
        "unclear",
    )
    base_leg = next((s for s in legs if s.name == "base"), None)
    base_return = base_leg.implied_return_pct if base_leg else None
    target = dossier.consensus.target_upside_pct if dossier.consensus else None
    derived = _derive_stance(base_return, target)
    if derived is not None:
        stance = derived
        stance_basis = f"base {base_return:+.0%} vs consensus target {target:+.0%}"
    else:
        stance_basis = "vs model-implied anchor; no consensus target"
    thesis = AlphaThesis(
        anchor=anchor,
        stance=stance,
        stance_basis=stance_basis,
        variant=str(data.get("variant") or ""),
        ...   # rest of the fields unchanged
    )
```

Update the stance line in `_synthesize_prompt` (the `"stance in [...]"` instruction). Replace:

```python
        "stance in [above_expectations, in_line, below_expectations, unclear] RELATIVE TO THE ANCHOR. "
```

with:

```python
        "stance in [above_consensus, in_line_consensus, below_consensus, unclear]. Your declared "
        "value is used ONLY when there is no consensus price target; otherwise the system derives "
        "stance deterministically from the base-case return vs consensus. "
```

- [ ] **Step 5: Update the mock + migrate old-enum literals**

In `saturn/llm/mock_client.py`, in `_ALPHA`, change `"stance": "in_line"` to `"stance": "in_line_consensus"`.

Migrate every remaining old-enum literal to a new value (mechanical):
- `tests/agents/test_synthesist.py:61` — `_complete_thesis` helper `stance="above_expectations"` → `stance="above_consensus"`.
- `tests/agents/test_synthesist.py:103` — `_valid_alpha_json` `"stance": "above_expectations"` → `"stance": "above_consensus"`.
- `tests/agents/test_synthesist.py:140` — `test_synthesize_builds_priced_thesis` assertion `t.stance == "above_expectations"` → `t.stance == "above_consensus"` (this dossier has no consensus target, so the LLM stance is kept).
- `tests/agents/test_critic.py:282` — `_alpha()` helper `stance="above_expectations"` → `stance="above_consensus"`.
- `tests/test_markdown_report.py:354` — `_alpha_thesis` helper `stance="above_expectations"` → `stance="above_consensus"`.

- [ ] **Step 6: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green (confirms the enum rename is fully migrated). If pytest reports any lingering `above_expectations`/`below_expectations`/bare `in_line` literal, fix it.

- [ ] **Step 7: Commit**

```bash
git add saturn/models.py saturn/agents/synthesist.py saturn/llm/mock_client.py tests/agents/test_synthesist.py tests/agents/test_critic.py tests/test_markdown_report.py
git commit -m "feat(synthesist): deterministic consensus-relative stance (derive from base return vs target)"
```

---

### Task 3: Stance render line + Critic stance↔Final-View check

**Files:**
- Modify: `saturn/reports/markdown_report.py` (`_render_alpha`), `saturn/agents/critic.py` (`_critic_prompt` `alpha_note`)
- Test: `tests/test_markdown_report.py`, `tests/agents/test_critic.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_markdown_report.py`, append:

```python
def test_render_alpha_shows_stance_basis():
    report = _sample_report()
    thesis = _alpha_thesis()
    thesis.stance_basis = "base +11% vs consensus target +45%"
    report.alpha_thesis = thesis
    md = render(report)
    assert "base +11% vs consensus target +45%" in md
```

In `tests/agents/test_critic.py`, append:

```python
def test_critic_prompt_has_stance_vs_final_view_check():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=_alpha())
    assert "Final View" in p and "stance" in p.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py tests/agents/test_critic.py -q -k "stance"`
Expected: FAIL (basis not rendered; instruction absent).

- [ ] **Step 3: Render the stance basis**

In `saturn/reports/markdown_report.py`, in `_render_alpha`, replace the stance line:

```python
    out.append(f"**Stance:** {thesis.stance.replace('_', ' ')} · confidence {thesis.confidence}")
```

with:

```python
    basis = f"  ({thesis.stance_basis})" if thesis.stance_basis else ""
    out.append(f"**Stance:** {thesis.stance.replace('_', ' ')} · confidence {thesis.confidence}{basis}")
```

- [ ] **Step 4: Add the Critic stance↔Final-View instruction**

In `saturn/agents/critic.py`, in `_critic_prompt`, extend the `alpha_note` string. It currently ends with `"...filing support).\n"`. Append one more sentence before the closing quote so `alpha_note` reads:

```python
    alpha_note = ("\nThe report includes an ALPHA THESIS. Also flag category "
                  "unsupported_alpha_inference when: the variant is not connected to the anchor; a "
                  "scenario driver has no support in the data; the falsifier is not an observable "
                  "event with a time window; or a conclusion is stronger than its evidence (e.g. an "
                  "accounting inference with no contract-liability / deferred-revenue / filing "
                  "support). Also flag it when the alpha STANCE contradicts the Final View — e.g. "
                  "stance below_consensus while the Final View reads as an aggressive buy.\n"
                  if alpha is not None else "")
```

- [ ] **Step 5: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py tests/agents/test_critic.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 6: Commit**

```bash
git add saturn/reports/markdown_report.py saturn/agents/critic.py tests/test_markdown_report.py tests/agents/test_critic.py
git commit -m "feat(alpha): render stance derivation basis + Critic checks stance vs Final View"
```

---

### Task 4: High-severity warning banner

**Files:**
- Modify: `saturn/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_markdown_report.py`)

```python
def test_render_high_severity_banner_present():
    from saturn.models import CriticReview, CriticFinding, Provenance
    report = _sample_report()
    report.critic_review = CriticReview(
        findings=[CriticFinding(claim="RPO coverage ratio internally inconsistent",
                  section="valuation_discussion", category="contradiction", verdict="contradicted",
                  evidence="7.6x vs 2.2x", severity="high")],
        claims_checked=10, summary="s", provenance=Provenance(source="Saturn (critic)"))
    md = render(report)
    assert "Unresolved high-severity audit finding" in md
    assert "RPO coverage ratio internally inconsistent" in md
    # banner sits after the Executive Summary and before the Alpha Thesis
    assert md.index("Unresolved high-severity") < md.index("## 2. Alpha Thesis")
    assert md.index("## 1. Executive Summary") < md.index("Unresolved high-severity")


def test_render_no_banner_without_high_findings():
    from saturn.models import CriticReview, CriticFinding, Provenance
    report = _sample_report()
    report.critic_review = CriticReview(
        findings=[CriticFinding(claim="minor", section="x", category="contradiction",
                  verdict="v", evidence="e", severity="low")],
        claims_checked=5, summary="s", provenance=Provenance(source="Saturn (critic)"))
    assert "Unresolved high-severity" not in render(report)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q -k "banner"`
Expected: FAIL (banner text absent).

- [ ] **Step 3: Implement the banner helper + placement**

In `saturn/reports/markdown_report.py`, add before `def render(`:

```python
def _render_high_severity_banner(review) -> list[str]:
    """A prominent warning block for UNRESOLVED high-severity Critic findings, so a wrong figure
    can't sit silently in the prose and only surface at §15. Empty when there are none."""
    if review is None:
        return []
    highs = [f for f in review.findings if f.severity == "high"]
    if not highs:
        return []
    out = ["> ⚠️ **Unresolved high-severity audit finding(s)** — treat the affected figures as provisional:"]
    for f in highs:
        claim = (f.claim or "")[:120]
        out.append(f"> - **[{f.section}]** {claim}")
    out.append("")
    return out
```

In `render()`, find the Executive Summary line and the alpha block right after it:

```python
    out += ["## 1. Executive Summary", "", a.executive_summary, ""]
    if report.alpha_thesis is not None:
        out += _render_alpha(report.alpha_thesis)
    else:
        out += ["## 2. Alpha Thesis", "", "_Alpha thesis unavailable this run._", ""]
```

Insert the banner between the Executive Summary and the alpha block:

```python
    out += ["## 1. Executive Summary", "", a.executive_summary, ""]
    out += _render_high_severity_banner(report.critic_review)
    if report.alpha_thesis is not None:
        out += _render_alpha(report.alpha_thesis)
    else:
        out += ["## 2. Alpha Thesis", "", "_Alpha thesis unavailable this run._", ""]
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_markdown_report.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/reports/markdown_report.py tests/test_markdown_report.py
git commit -m "feat(report): high-severity audit warning banner under the Executive Summary"
```

---

### Task 5: Self-repair handles cross-section contradictions

**Files:**
- Modify: `saturn/agents/critic.py` (`revise`)
- Test: `tests/agents/test_critic.py`

- [ ] **Step 1: Write the failing test** (append to `tests/agents/test_critic.py`)

```python
class _CaptureReviseLLM:
    """Records the section keys revise() offered for correction (parsed from the prompt)."""
    def __init__(self):
        self.offered_sections = None
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        import json, re
        m = re.search(r'CURRENT SECTION TEXT \(JSON\):\n(\{.*?\})\n\n', prompt, re.S)
        self.offered_sections = set(json.loads(m.group(1)).keys()) if m else set()
        return '{"bear_thesis": "corrected"}'


def test_revise_contradiction_widens_scope_to_all_sections():
    # A contradiction finding NAMED on executive_summary must let revise edit OTHER sections too
    # (the wrong value may live elsewhere — the MSFT RPO case).
    from saturn.agents.critic import revise
    review = _rev([_find("contradiction", "high", "executive_summary")])
    llm = _CaptureReviseLLM()
    revise(_analysis(), _debate(), review, _dossier(), llm)
    assert "bear_thesis" in llm.offered_sections          # a non-named section was offered
    assert "valuation_discussion" in llm.offered_sections


def test_revise_non_contradiction_stays_scoped_to_named_section():
    from saturn.agents.critic import revise
    review = _rev([_find("unsupported_number", "high", "financial_snapshot")])
    llm = _CaptureReviseLLM()
    revise(_analysis(), _debate(), review, _dossier(), llm)
    assert llm.offered_sections == {"financial_snapshot"}   # named-section-only scope preserved
```

(`_rev`, `_find`, `_analysis`, `_debate`, `_dossier` already exist in `test_critic.py`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q -k "revise_contradiction or revise_non_contradiction"`
Expected: FAIL (contradiction scope is currently named-section-only, so `bear_thesis` is not offered).

- [ ] **Step 3: Widen `revise` scope for contradictions**

In `saturn/agents/critic.py`, in `revise`, find the `affected` computation. Current:

```python
        actionable = [f for f in review.findings if _is_actionable_finding(f)]
        sections = {**analysis.model_dump(), **debate.model_dump()}
        affected = sorted({f.section for f in actionable if f.section in sections})
        if not affected:
            return None
```

Replace with (contradictions get whole-report scope so revise can fix whichever side is wrong):

```python
        actionable = [f for f in review.findings if _is_actionable_finding(f)]
        sections = {**analysis.model_dump(), **debate.model_dump()}
        if any(f.category == "contradiction" for f in actionable):
            affected = sorted(sections.keys())
        else:
            affected = sorted({f.section for f in actionable if f.section in sections})
        if not affected:
            return None
```

- [ ] **Step 4: Run to verify pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q` → PASS (existing self-repair keep-if-better tests stay green — the strict score-gate in `run()` is unchanged).
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/critic.py tests/agents/test_critic.py
git commit -m "feat(critic): self-repair edits all sections for contradiction findings (cross-section fix)"
```

---

## Final verification (live)

After all tasks pass, regenerate the MSFT report and confirm the three fixes end-to-end:

```bash
.venv/Scripts/python.exe -m saturn.cli research MSFT
```

In `reports/MSFT_<date>.md`:
1. **RPO** — the Key Metrics table shows `rpo_to_ttm_revenue` and `rpo_to_annualized_quarterly_revenue` with explicit formulas; the prose no longer fabricates a mislabelled 7.6x, or if it still cites a coverage ratio it matches a named metric.
2. **Stance** — §2 shows a consensus-relative stance with the derivation line (e.g. `below consensus (base +X% vs consensus target +Y%)`), consistent with the scenario table.
3. **Banner** — if any high-severity finding remains after self-repair, a warning block appears right under §1; otherwise none. Check whether self-repair now resolves a cross-section contradiction (fewer/no high findings than the prior MSFT run).

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §2 RPO → Task 1; §3 stance enum+derive+prompt+render+critic → Tasks 2–3; §4a banner → Task 4; §4b cross-section revise → Task 5. Migration note (enum literals) → Task 2 Step 5. All covered.
- **Type consistency:** stance enum `above_consensus|in_line_consensus|below_consensus|unclear` used identically in models.py, `_derive_stance`, `_one_of`, prompt, and every migrated test; `stance_basis` added in Task 2, rendered in Task 3; `_derive_stance(base_return, target_upside)` and `_render_high_severity_banner(review)` signatures consistent.
- **Deferred (spec §6):** probability/EV column, structured confirmer, rationale length cap — not in any task, by design.

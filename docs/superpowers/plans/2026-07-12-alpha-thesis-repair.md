# Alpha-Thesis Auto-Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Critic self-repair loop to the alpha thesis — when the Critic flags a high/medium finding on the alpha thesis, revise its prose fields and re-verify with keep-if-better, instead of only surfacing it via the §1 banner.

**Architecture:** A second, sequential self-repair loop in `run()` that mirrors the existing section loop. `revise_alpha` (critic) generates corrected prose; `apply_alpha_corrections` (synthesist) splices ONLY the prose fields and recomputes completeness; the same strict `_score(revised) < _score(original)` gate accepts or rejects. Deterministic `stance`/`scenarios`/`anchor` are never touched.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-alpha-thesis-repair-design.md`.

**File map:**
- `saturn/models.py` — `ALPHA_PROSE_FIELDS` constant (Task 1)
- `saturn/agents/critic.py` — `_is_alpha_actionable`/`_alpha_actionable` (Task 1), `REVISE_ALPHA_SYSTEM` + `revise_alpha` (Task 2)
- `saturn/agents/synthesist.py` — `apply_alpha_corrections` (Task 3)
- `saturn/workflows/equity_research.py` — second repair loop + imports (Task 4)

---

### Task 1: `ALPHA_PROSE_FIELDS` constant + trigger helpers

**Files:**
- Modify: `saturn/models.py`, `saturn/agents/critic.py`
- Test: `tests/test_models_alpha.py`, `tests/agents/test_critic.py`

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_models_alpha.py`:
```python
def test_alpha_prose_fields_excludes_derived():
    from saturn.models import ALPHA_PROSE_FIELDS
    # derived/computed fields must never be LLM-rewritable
    assert "stance" not in ALPHA_PROSE_FIELDS and "scenarios" not in ALPHA_PROSE_FIELDS
    assert "stance_basis" not in ALPHA_PROSE_FIELDS and "anchor" not in ALPHA_PROSE_FIELDS
    # the prose fields are present
    for f in ("variant", "rationale", "key_variable", "falsifier", "horizon"):
        assert f in ALPHA_PROSE_FIELDS
```

APPEND to `tests/agents/test_critic.py`:
```python
def test_is_alpha_actionable_matrix():
    from saturn.agents.critic import _is_alpha_actionable, _alpha_actionable
    assert _is_alpha_actionable(_find("unsupported_alpha_inference", "high", "alpha_thesis")) is True
    assert _is_alpha_actionable(_find("contradiction", "medium", "alpha_thesis / final_view")) is True
    assert _is_alpha_actionable(_find("unsupported_alpha_inference", "low", "alpha_thesis")) is False
    assert _is_alpha_actionable(_find("contradiction", "high", "bull_thesis")) is False
    assert _alpha_actionable(_rev([_find("unsupported_alpha_inference", "high", "alpha_thesis")])) is True
    assert _alpha_actionable(_rev([_find("contradiction", "high", "bull_thesis")])) is False
```
(`_find(category, severity, section)` and `_rev(findings)` already exist in `test_critic.py`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py tests/agents/test_critic.py -q -k "alpha_prose or alpha_actionable"`
Expected: FAIL (`ALPHA_PROSE_FIELDS` / `_is_alpha_actionable` not defined).

- [ ] **Step 3: Add the constant (models.py)**

In `saturn/models.py`, immediately AFTER the `class AlphaThesis` definition (after its last field `provenance`), add:
```python
# Alpha-thesis fields the LLM may rewrite during self-repair. Deterministic/computed fields
# (stance, stance_basis, anchor, scenarios, incompleteness) are deliberately excluded.
ALPHA_PROSE_FIELDS = ("variant", "rationale", "key_variable", "falsifier", "horizon")
```

- [ ] **Step 4: Add the trigger helpers (critic.py)**

In `saturn/agents/critic.py`, next to `_is_actionable_finding` / `_actionable` (near line 208), add:
```python
def _is_alpha_actionable(f) -> bool:
    """A high/medium finding on the alpha thesis — repairable by rewriting its prose fields.
    (Kept separate from _is_actionable_finding: alpha findings do not trigger section revise.)"""
    return f.severity in ("high", "medium") and (f.section or "").startswith("alpha_thesis")


def _alpha_actionable(review: CriticReview) -> bool:
    return any(_is_alpha_actionable(f) for f in review.findings)
```

- [ ] **Step 5: Run to verify they pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_models_alpha.py tests/agents/test_critic.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 6: Commit**
```bash
git add saturn/models.py saturn/agents/critic.py tests/test_models_alpha.py tests/agents/test_critic.py
git commit -m "feat(critic): ALPHA_PROSE_FIELDS + alpha-thesis actionable-finding helpers"
```
Commit trailer (all commits): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: `revise_alpha` + `REVISE_ALPHA_SYSTEM`

**Files:**
- Modify: `saturn/agents/critic.py`
- Test: `tests/agents/test_critic.py`

- [ ] **Step 1: Write the failing tests** (APPEND to `tests/agents/test_critic.py`)

```python
class _AlphaReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=revise_alpha" in prompt
        # a corrected rationale PLUS stray derived keys that MUST be dropped
        return ('{"rationale": "corrected: ~3.9% 2-year FCF CAGR", '
                '"stance": "above_consensus", "scenarios": []}')


class _BadAlphaReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return "not json"


def test_revise_alpha_returns_prose_only():
    from saturn.agents.critic import revise_alpha
    findings = [_find("unsupported_alpha_inference", "high", "alpha_thesis")]
    corr = revise_alpha(_alpha(), _dossier(), findings, _AlphaReviseLLM())
    assert corr == {"rationale": "corrected: ~3.9% 2-year FCF CAGR"}   # stance/scenarios dropped


def test_revise_alpha_soft_fails_to_none():
    from saturn.agents.critic import revise_alpha
    findings = [_find("unsupported_alpha_inference", "high", "alpha_thesis")]
    assert revise_alpha(_alpha(), _dossier(), findings, _BadAlphaReviseLLM()) is None
```
(`_alpha()` builds an `AlphaThesis`, `_dossier()` a `CompanyDossier`, `_find` a finding — all exist in `test_critic.py`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q -k "revise_alpha"`
Expected: FAIL (`revise_alpha` not defined).

- [ ] **Step 3: Implement** (add to `saturn/agents/critic.py`, e.g. after the existing `revise`)

```python
REVISE_ALPHA_SYSTEM = (
    "You are correcting the ALPHA THESIS of an equity research report. You are given specific "
    "VERIFIED problems on the thesis (each with the underlying data as evidence) and the current "
    "text of its prose fields. Rewrite ONLY those prose fields to fix exactly these problems using "
    "the cited data. Do NOT change the stance, do NOT change the scenario numbers or assumptions, "
    "and do NOT invent figures — preserve everything else in each field. Respond with ONLY a JSON "
    "object mapping each affected prose-field name to its corrected full text (plain strings), no "
    "prose, no code fences."
)


def revise_alpha(alpha, dossier: CompanyDossier, findings, llm, *, model: str | None = None) -> dict | None:
    """Return {prose_field: corrected_text} for the alpha thesis, or None (soft-fail). Only the
    ALPHA_PROSE_FIELDS are rewritten; any stance/scenarios/anchor key the model returns is dropped."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    from saturn.models import ALPHA_PROSE_FIELDS
    try:
        problems = "\n".join(
            f'- ({f.category}, {f.severity}): "{f.claim}" -- {f.evidence}' for f in findings
        )
        current = {k: getattr(alpha, k) for k in ALPHA_PROSE_FIELDS}
        prompt = (
            "OUTPUT_SCHEMA=revise_alpha\n"
            "VERIFIED PROBLEMS to fix:\n" + problems + "\n\n"
            "CURRENT ALPHA PROSE (JSON):\n" + json.dumps(current) + "\n\n"
            "UNDERLYING DATA (provenance-tagged):\n" + _company_context(dossier) + "\n\n"
            f"Return ONLY a JSON object mapping affected fields (subset of {list(ALPHA_PROSE_FIELDS)}) "
            "to corrected full text. Do NOT include stance, scenarios, or anchor."
        )
        raw = llm.complete(REVISE_ALPHA_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
        data = json.loads(_extract_json(raw))
        out = {k: str(v) for k, v in data.items() if k in ALPHA_PROSE_FIELDS and isinstance(v, str)}
        return out or None
    except Exception as exc:  # noqa: BLE001 - best-effort; keep the original alpha thesis
        logger.warning("critic revise_alpha unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
```

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_critic.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**
```bash
git add saturn/agents/critic.py tests/agents/test_critic.py
git commit -m "feat(critic): revise_alpha — regenerate alpha-thesis prose to fix flagged findings"
```

---

### Task 3: `apply_alpha_corrections`

**Files:**
- Modify: `saturn/agents/synthesist.py`
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing tests** (APPEND to `tests/agents/test_synthesist.py`)

```python
def test_apply_alpha_corrections_splices_prose_and_preserves_derived():
    from saturn.agents.synthesist import apply_alpha_corrections
    orig = _complete_thesis()
    updated = apply_alpha_corrections(orig, {"rationale": "new rationale",
                                             "stance": "unclear", "scenarios": []})
    assert updated.rationale == "new rationale"        # prose spliced
    assert updated.stance == orig.stance               # derived stance untouched
    assert updated.scenarios == orig.scenarios         # scenarios untouched
    assert updated.anchor == orig.anchor               # anchor untouched


def test_apply_alpha_corrections_recomputes_incompleteness():
    from saturn.agents.synthesist import apply_alpha_corrections
    # emptying the falsifier should make the completeness gate flag it
    updated = apply_alpha_corrections(_complete_thesis(), {"falsifier": ""})
    assert any("falsifier" in g for g in updated.incompleteness)
```
(`_complete_thesis()` already exists in `test_synthesist.py` and builds a complete `AlphaThesis`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k "apply_alpha"`
Expected: FAIL (`apply_alpha_corrections` not defined).

- [ ] **Step 3: Implement** (add to `saturn/agents/synthesist.py`, near `alpha_completeness`)

```python
def apply_alpha_corrections(alpha, corrections: dict):
    """Splice corrected prose fields into the alpha thesis and recompute completeness. Only
    ALPHA_PROSE_FIELDS are updated; stance/stance_basis/anchor/scenarios are carried over verbatim
    by model_copy."""
    from saturn.models import ALPHA_PROSE_FIELDS
    updated = alpha.model_copy(update={k: v for k, v in corrections.items() if k in ALPHA_PROSE_FIELDS})
    updated.incompleteness = alpha_completeness(updated)
    return updated
```

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**
```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): apply_alpha_corrections — splice prose + recompute completeness"
```

---

### Task 4: Wire the alpha-repair loop into `run()`

**Files:**
- Modify: `saturn/workflows/equity_research.py`
- Test: `tests/test_equity_research_workflow.py`

- [ ] **Step 1: Write the failing tests** (APPEND to `tests/test_equity_research_workflow.py`)

```python
_ALPHA_JSON = json.dumps({
    "stance": "below_consensus", "variant": "v", "rationale": "3-year CAGR near zero",
    "confidence": "medium", "key_variable": "k", "falsifier": "GM<60% in 2Q", "horizon": "12m",
    "scenarios": [
        {"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 13.0, "multiple": 18.0, "multiple_basis": "P/E"},
        {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
        {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 10.0, "multiple_basis": "P/E"}]})


class _AlphaRepairLLM:
    """analyze -> debate -> synth -> critic(1 high alpha finding) -> revise_alpha -> critic(clean)."""
    def __init__(self, improve=True):
        self.improve = improve
        self.critic_calls = 0
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            return _ALPHA_JSON
        if "OUTPUT_SCHEMA=revise_alpha" in prompt:
            return json.dumps({"rationale": "corrected rationale"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            self.critic_calls += 1
            finding = [{"claim": "3-year CAGR near zero", "section": "alpha_thesis",
                        "category": "unsupported_alpha_inference", "verdict": "unsupported",
                        "evidence": "data shows 3.9% 2-year", "severity": "high"}]
            if self.critic_calls == 1:
                return json.dumps({"claims_checked": 3, "summary": "x", "findings": finding})
            return json.dumps({"claims_checked": 3, "summary": "ok",
                               "findings": [] if self.improve else finding})
        return "{}"


def test_run_alpha_self_repair_corrects_and_flags():
    r = run(_mock_dossier("JNJ"), _AlphaRepairLLM(improve=True), model_used="m", mock=False)
    assert r.alpha_thesis is not None and r.alpha_thesis.rationale == "corrected rationale"
    assert r.critic_review is not None and r.critic_review.repaired is True


def test_run_alpha_self_repair_keeps_original_when_not_improved():
    r = run(_mock_dossier("JNJ"), _AlphaRepairLLM(improve=False), model_used="m", mock=False)
    assert r.alpha_thesis.rationale == "3-year CAGR near zero"      # revision rejected
    assert r.critic_review is None or r.critic_review.repaired is False
```
(`json`, `run`, `_mock_dossier`, `_ANALYSIS_KEYS` are already imported/defined at the top of this test file.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q -k "alpha_self_repair"`
Expected: FAIL (`run()` does not yet repair the alpha thesis; `rationale` stays "3-year CAGR near zero").

- [ ] **Step 3: Implement**

In `saturn/workflows/equity_research.py`, update the two agent imports:
```python
from saturn.agents.critic import (
    _actionable, _alpha_actionable, _is_alpha_actionable, _score, critique, revise, revise_alpha,
)
from saturn.agents.synthesist import apply_alpha_corrections, synthesize
```

In `run()`, immediately AFTER the existing section-repair block (the `if review is not None and _actionable(review):` block, ending with `analysis, deb, review = r_analysis, r_deb, r_review`) and BEFORE the `return ResearchReport(...)`, add:
```python
    # Alpha-thesis self-repair: the section loop above never touches the structured AlphaThesis.
    # When the Critic flags a high/medium finding on it, rewrite ONLY its prose fields and re-verify
    # under the same keep-if-better gate (stance/scenarios/anchor stay deterministic).
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

- [ ] **Step 4: Run to verify they pass + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_equity_research_workflow.py -q` → PASS (existing self-repair tests stay green).
Run: `.venv/Scripts/python.exe -m pytest -q` → all green.

- [ ] **Step 5: Commit**
```bash
git add saturn/workflows/equity_research.py tests/test_equity_research_workflow.py
git commit -m "feat(workflow): alpha-thesis self-repair loop (revise_alpha + re-verify, keep-if-better)"
```

---

## Final verification (live)

Regenerate JNJ and confirm the fix end-to-end:
```bash
.venv/Scripts/python.exe -m saturn.cli research JNJ
```
In `reports/JNJ_<date>.md`:
1. The alpha §2 rationale no longer mislabels the FCF CAGR ("3-year … near zero" → the correct ~3.9% 2-year figure), i.e. the alpha prose was auto-corrected.
2. The §1 high-severity banner no longer lists that `unsupported_alpha_inference` finding (self-repair removed it), and §15 shows "Auto-corrected by the Critic (self-repair)".
3. Sanity: §2 stance, stance_basis derivation line, and the scenario table/prices are unchanged in form (only prose changed).

Then finish the branch (PR to `main`).

---

## Self-review notes (author)

- **Spec coverage:** §2 guardrail → Task 1 (`ALPHA_PROSE_FIELDS`); §3 helpers+revise_alpha → Tasks 1–2; §4 apply → Task 3; §5 wiring → Task 4; §7 tests distributed across tasks; §9 out-of-scope honored (no scenario/stance rewrite — `apply_alpha_corrections` filters to prose only, and its test asserts stance/scenarios/anchor unchanged).
- **Type consistency:** `revise_alpha(alpha, dossier, findings, llm, *, model)` → returns `dict|None`; `apply_alpha_corrections(alpha, corrections)` → `AlphaThesis`; `_is_alpha_actionable(f)`/`_alpha_actionable(review)`; `ALPHA_PROSE_FIELDS` used identically in models/critic/synthesist. All aligned across tasks.
- **No placeholders:** every step has complete code.

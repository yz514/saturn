# v1.2 Stance/Rationale Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the LLM's alpha rationale consistent with the deterministically-derived stance by reframing the synthesist's instructions (prompt-only; no logic change, no extra LLM call).

**Architecture:** Replace the verdict-asserting clause in `SYNTHESIZE_SYSTEM` with one that frames the rationale around the base-case return vs the anchor and forbids re-declaring the stance. Everything else (`_derive_stance`, `_build_thesis`, enum, render) is unchanged.

**Tech Stack:** Python 3.13, pytest. Venv python: `.venv/Scripts/python.exe`. Spec: `docs/superpowers/specs/2026-07-12-stance-rationale-alignment-design.md`.

---

### Task 1: Reframe SYNTHESIZE_SYSTEM so the rationale tracks the derived stance

**Files:**
- Modify: `saturn/agents/synthesist.py` (`SYNTHESIZE_SYSTEM` string only)
- Test: `tests/agents/test_synthesist.py`

- [ ] **Step 1: Write the failing test** — APPEND to `tests/agents/test_synthesist.py`:

```python
def test_synthesize_system_frames_rationale_and_forbids_verdict():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    s = SYNTHESIZE_SYSTEM.lower()
    # rationale must be framed on the base-case-vs-anchor axis...
    assert "base-case" in s and "rationale" in s
    # ...and the model must NOT assert its own overall verdict / re-declare the stance
    assert "do not assert" in s
    assert "the system derives" in s
    # the old verdict-assertion clause is gone
    assert "state whether the view is above / in line with / below the anchor and why" not in s
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k "frames_rationale"`
Expected: FAIL (the new phrases are absent; the old clause is still present).

- [ ] **Step 3: Edit `SYNTHESIZE_SYSTEM`**

In `saturn/agents/synthesist.py`, the constant currently reads:

```python
SYNTHESIZE_SYSTEM = (
    "You are a portfolio manager turning an analyst's memo into a tradeable view. You are given "
    "the market-expectation ANCHOR, the draft report, and the underlying data. State whether the "
    "view is above / in line with / below the anchor and WHY, grounded in specific data. If you "
    "cannot honestly take a differentiated view, return stance 'unclear' — never manufacture one. "
    "Give the single key variable that decides it, an OBSERVABLE falsifier (a concrete event plus a "
    "time window), a horizon, and exactly three scenarios (bull/base/bear). Each scenario states a "
    "period, a per-share metric with its value and basis, and a multiple with its basis — do NOT "
    "output prices; the system computes price = value x multiple. Keep 'variant' to ONE sentence "
    "under 35 words. Respond with ONLY a single valid JSON object, no prose, no code fences."
)
```

Replace the whole constant with (only the second/third sentences change — the two-sentence verdict clause becomes the reframed alignment clause; the rest is byte-identical):

```python
SYNTHESIZE_SYSTEM = (
    "You are a portfolio manager turning an analyst's memo into a tradeable view. You are given "
    "the market-expectation ANCHOR, the draft report, and the underlying data. Write the RATIONALE "
    "around how your base-case scenario's return compares to the anchor — the consensus target "
    "upside, or the model-implied expectation — e.g. 'our base case implies +X% vs the Street's "
    "+Y%, below/above because ...', grounded in specific data. Do NOT assert an overall 'consistent "
    "with / differentiated from consensus' verdict and do NOT re-state the stance label in prose: "
    "the system derives and labels the stance deterministically from your base-case return vs "
    "consensus. Still return a 'stance' field — it is used only as a fallback when there is no "
    "consensus target; if you cannot take a differentiated view there, use 'unclear' and never "
    "manufacture one. "
    "Give the single key variable that decides it, an OBSERVABLE falsifier (a concrete event plus a "
    "time window), a horizon, and exactly three scenarios (bull/base/bear). Each scenario states a "
    "period, a per-share metric with its value and basis, and a multiple with its basis — do NOT "
    "output prices; the system computes price = value x multiple. Keep 'variant' to ONE sentence "
    "under 35 words. Respond with ONLY a single valid JSON object, no prose, no code fences."
)
```

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/agents/test_synthesist.py -q -k "frames_rationale"` → PASS.
Run: `.venv/Scripts/python.exe -m pytest -q` → all green (no other behavior changed; `synthesize`'s JSON contract and every downstream test are unaffected).

- [ ] **Step 5: Commit**

```bash
git add saturn/agents/synthesist.py tests/agents/test_synthesist.py
git commit -m "feat(synthesist): reframe rationale prompt to track the derived stance (no verdict assertion)"
```
Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Final verification (live)

Regenerate the MSFT report and confirm the fix end-to-end:

```bash
.venv/Scripts/python.exe -m saturn.cli research MSFT
```

In `reports/MSFT_<date>.md`:
1. §2 rationale reads consistently with the derived stance — it frames the base-case-vs-consensus comparison (e.g. "base case implies +X% vs the Street's +Y%, below because …") rather than declaring the view "consistent with consensus."
2. §15 no longer contains the stance-vs-rationale `unsupported_alpha_inference` finding that appeared in the v1.1 run (and, correspondingly, no §1 banner for that finding).

Then finish the branch (PR to `main`, once #28 has merged so this rebases cleanly).

---

## Self-review notes (author)

- **Spec coverage:** §2 fix → Task 1 Step 3 (exact replacement text matches the spec's replacement clause). §4 testing → Task 1 Steps 1/4 + the live verification. §3 "what does not change" honored — only the `SYNTHESIZE_SYSTEM` string and one test are touched.
- **No placeholders:** the full before/after constant is shown.
- **Consistency:** the test's asserted phrases ("base-case", "do not assert", "the system derives") are all present in the replacement text; the removed clause string matches the old text exactly (lower-cased in the assertion).

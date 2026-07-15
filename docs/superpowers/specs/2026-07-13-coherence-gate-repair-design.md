# Coherence Gate Slice 2 — bull-below-spot check + stronger repair — Design

**Date:** 2026-07-13
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user
**Builds on:** the scenario-coherence gate (PR #35, merged to `main`) and the NTM horizon fix.

## 1. Goal & honest framing

The coherence gate (Slice 1) reliably **detects** incoherent scenario tables but its single
corrective re-synthesis often **fails to repair** them. On a verified MRVL run it flagged
`prose_vs_computed` and `multiple_horizon`, re-synthesized once, and the LLM produced the same
malformed table (base −47% vs prose −5%; bull priced at −19%, below spot) — so the report shipped
with a banner rather than a fixed thesis. The mechanical root cause: the LLM anchors the multiple to
the forward P/E it sees (~38–40x) but applies it to a near-term EPS ($3–4), so every scenario
underprices.

This slice does two things: (A) adds the deferred **bull-below-spot** detection check, and (B)
makes the repair **stronger and more reliable** — a root-cause prompt fix plus a bounded multi-pass.
Detection was never the problem; repair is. The prompt fix is the primary lever (it also improves
the *first* synthesis, so the gate fires less often); the multi-pass is a bounded amplifier.

## 2. Part A — `bull_below_spot` detection check

A fourth deterministic check in `scenario_coherence(thesis, dossier)` (`saturn/agents/synthesist.py`),
appended after the `multiple_horizon` block so the stable issue order is
`[monotonicity, prose_vs_computed, multiple_horizon, bull_below_spot]`:

```python
    # 4. Bull-below-spot — a "bull" scenario that loses money. Unambiguously wrong unless the stance
    # is itself bearish (below_consensus), where a below-spot bull can be a deliberate short.
    if bull is not None and bull.implied_return_pct is not None and bull.implied_return_pct < 0:
        sev = "medium" if thesis.stance == "below_consensus" else "high"
        issues.append(CoherenceIssue(
            check="bull_below_spot", severity=sev,
            detail=(f"bull scenario returns {bull.implied_return_pct:+.0%} (below spot) despite a "
                    f"{thesis.stance} stance")))
```

Severity: **`medium` for a `below_consensus` stance** (a below-spot bull may be a deliberate short),
**`high` otherwise** (`above_consensus`/`in_line_consensus`/`unclear` — a losing bull is
unambiguously wrong). Uses the already-computed `bull.implied_return_pct`; no new inputs. The `high`
weight (2) / `medium` weight (1) feed the existing `_coherence_score`.

**Data model (`saturn/models.py`):** add the literal value —
`check: Literal["monotonicity", "prose_vs_computed", "multiple_horizon", "bull_below_spot"]`.

The §2 render banner already lists any `CoherenceIssue` generically — **no render change**.

## 3. Part B — stronger repair: root-cause prompt fix

### 3.1 `SYNTHESIZE_SYSTEM` (`saturn/agents/synthesist.py`)

Insert a horizon-matching rule immediately before the final
`"Respond with ONLY a single valid JSON object, no prose, no code fences."` sentence:

```
"CRITICAL — horizon match: the multiple and the per-share value must be the SAME horizon. If you "
"use a forward (next-fiscal-year) P/E, pair it with the forward EPS; NEVER apply a forward multiple "
"to a trailing or near-term EPS — that mechanically underprices every scenario. Before finalizing, "
"verify bull >= base >= bear in implied price and that the base-case return you describe matches the "
"base scenario. "
```

This affects **every** synthesis (intended — a general correctness improvement), so clean runs like
AVGO get it too. It reduces how often the gate fires at all.

### 3.2 `resynthesize_coherent` corrective prompt

Append a **worked arithmetic hint** to the existing `corrective` string, built from the real numbers
when available (guarded — omitted when consensus P/E or the driver model is absent):

```python
        cons, dm = dossier.consensus, dossier.driver_model
        hint = ""
        if cons and cons.forward_pe and cons.forward_eps and dm and dm.saturn_eps:
            hint = (f" For reference, the stock trades at ~{cons.forward_pe:.0f}x its forward EPS "
                    f"${cons.forward_eps:.2f} (which equals spot). Applying ~{cons.forward_pe:.0f}x "
                    f"to a near-term EPS like ${dm.saturn_eps:.2f} yields a price far below spot — "
                    f"that is the horizon error. Either pair forward EPS with the forward multiple, "
                    f"or use a near-term multiple (~15-20x) with the near-term EPS.")
        corrective = (
            "\n\nYour previous scenario table failed these coherence checks: " + problems + ". "
            "Regenerate the FULL thesis so that: bull >= base >= bear in implied price; any P/E "
            "multiple matches the horizon of its EPS (do NOT apply a next-fiscal-year multiple to a "
            "near-term EPS); and the base-case return you describe in the rationale matches the base "
            "scenario you output. Do NOT output prices." + hint
        )
```

## 4. Part C — bounded multi-pass repair (`saturn/workflows/equity_research.py`)

Replace the single-shot gate (current lines ~398–400) with a bounded loop that keeps each pass only
if it strictly improves and breaks early on no-improvement:

```python
    # Scenario-coherence gate: when the priced scenario table is incoherent, re-synthesize (up to
    # _MAX_COHERENCE_REPAIRS times) and keep each pass only if _coherence_score strictly improves;
    # stop as soon as a pass fails to improve (bounds cost on tables that can't be repaired, e.g. a
    # legitimately-bearish thesis whose bull is intrinsically below spot). Runs before critique.
    attempts = 0
    while alpha is not None and alpha.coherence_issues and attempts < _MAX_COHERENCE_REPAIRS:
        attempts += 1
        r_alpha = resynthesize_coherent(analysis, deb, company, llm, alpha.coherence_issues, model=call_model)
        if r_alpha is None or _coherence_score(r_alpha) >= _coherence_score(alpha):
            break
        alpha = r_alpha
```

Add a module constant `_MAX_COHERENCE_REPAIRS = 2` near the top of `equity_research.py`.

**Cost:** worst case **+2 Opus calls**, only when incoherent. The `break` on no-improvement means a
table that can't be improved (intrinsic `bull_below_spot` on a real short) costs just **+1** and then
banners honestly — no runaway. The end-of-`run()` `scenario_coherence` recompute (the stale-banner
fix from Slice 1) is unchanged and still reflects the final thesis.

## 5. Files

- **Modify:** `saturn/models.py` (add the literal value), `saturn/agents/synthesist.py`
  (`bull_below_spot` check, `SYNTHESIZE_SYSTEM` horizon rule, `resynthesize_coherent` arithmetic
  hint), `saturn/workflows/equity_research.py` (bounded loop + `_MAX_COHERENCE_REPAIRS`); touched
  tests. **No render change.**

## 6. Testing

- **`bull_below_spot` (unit, no LLM):** a priced bull with negative return and a non-bearish stance
  (`above`/`in_line`/`unclear`) → one `[high] bull_below_spot`; the same with `below_consensus` stance
  → `[medium]`; a bull with return ≥ 0 → no issue; a bull with `implied_return_pct is None` → no
  issue. A MRVL-like fixture (bull −19%, `below_consensus`) → the `bull_below_spot` issue is `medium`
  and appears last in issue order.
- **`SYNTHESIZE_SYSTEM`:** assert the string contains the horizon rule (e.g. `"same horizon"` and
  `"NEVER apply a forward multiple"`).
- **`resynthesize_coherent` corrective prompt:** with a stub LLM that captures the prompt, assert the
  corrective text includes the arithmetic hint and the consensus `forward_pe`/`forward_eps` and
  driver `saturn_eps` numbers when present; assert it omits the hint (no crash) when
  `dossier.consensus`/`driver_model` is absent.
- **Bounded multi-pass (`run()` integration, stateful stub LLM):**
  - pass 1 improves-but-still-incoherent, pass 2 makes it coherent → final `coherence_issues == []`
    with **exactly 2** re-synthesis calls;
  - pass 1 does not improve → **1** re-synthesis call, original kept, banner shows the issue;
  - already-coherent first synthesis → **0** re-synthesis calls;
  - a table that keeps improving would still stop at `_MAX_COHERENCE_REPAIRS` (2) — assert the cap by
    a stub that always "improves" and confirming no more than 2 calls.
- **Live:** regenerate MRVL and AVGO. MRVL: confirm the repair either clears more issues than before
  or still banners (now possibly including a `medium bull_below_spot`), and note the re-synthesis
  count. AVGO: confirm it stays coherent (no regression from the `SYNTHESIZE_SYSTEM` change).

## 7. Out of scope

- Deterministic auto-correction of scenario legs (still LLM-owned judgment).
- Changing the stance derivation, the anchor, or the driver model.
- More than 2 repair passes.
- Smarter repair-triggering: a lone `medium bull_below_spot` still enters the loop, but the
  no-improvement `break` self-limits it to a single wasted call — accepted as a minor inefficiency,
  not worth the added branching this slice.

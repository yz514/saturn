# Fiscal-Year-Aware NTM Blending (+ waterfall sign, prose tolerance) — Design

**Date:** 2026-07-15
**Status:** Approved (brainstorm) → ready for plan
**Builds on:** the consensus/driver stack (PRs #34–#37), all merged.

## 1. Goal & honest framing

Three verified defects, all deterministic, all in the consensus→driver→anchor path.

**(1) The consensus anchor contradicts the driver bridge.** `forward_pe` comes from `.info` and is
**FY+1-based**; the driver bridge prints `forward_eps_ntm`, which is the **`0y`** (current fiscal year)
row. Verified live:

| | `price ÷ forward_pe` | `forward_eps_ntm` (0y) | gap |
|---|---|---|---|
| MSFT | $19.37 (= `forward_eps`, FY+1) | **$16.82** | **15.2%** |
| AMZN | $9.89 (= `forward_eps`, FY+1) | **$8.66** | **14.1%** |

A reader cannot reconcile "forward P/E 20.4x" with "consensus NTM EPS $16.82". Worse, the root cause
**flips the headline EPS-gap sign**: MSFT reads Saturn *above* consensus (+$2.04) when the
horizon-correct figure makes it −$0.50.

The deeper bug: **`0y` is a valid NTM proxy only early in a fiscal year.** Late in (or past) the FY it
collapses toward TTM. MSFT's FY ended 2026-06-30 — `0y` is a fully-elapsed year. Slice 1 replaced `+1y`
with `0y` because MRVL (early in its FY) made `0y` ≈ NTM; that was right for MRVL and wrong for MSFT.
**Which row means "next twelve months" depends on fiscal-year progress.**

Fix: the standard NTM construction — a **fiscal-year-progress-weighted blend of FY0 and FY1** — and
speak the whole report in that one NTM number.

**(2) Waterfall attribution sign is inverted.** `eps_gap` is `saturn − consensus`, but the attribution
legs sum to `consensus − saturn` — **opposite by construction**. AMZN renders `gap $+0.71` next to
`-0.08 · -0.63`.

**(3) Prose base-return tolerance is far too loose.** `_PROSE_RETURN_TOL = 0.15` (15pp) was calibrated on
MRVL's gross +6%-vs-−47% case. AMZN's prose said **+12%** while the table computed **+22%** — a 10pp
contradiction that slipped through uncorrected and unflagged.

## 2. Part 1 — fiscal-year-aware NTM blending

### 2.1 The blend

```
w         = clamp( max(0, (fy0_end − today).days / 30.44) / 12 , 0, 1)   # FY0's share of the next 12m
NTM_eps   = w·eps_fy0 + (1−w)·eps_fy1
NTM_rev   = w·rev_fy0 + (1−w)·rev_fy1
```

`fy0_end` = the current fiscal year's end date, from `.info["nextFiscalYearEnd"]` (epoch seconds).
Verified available for MSFT / AMZN / MRVL. Worked examples (today = 2026-07-15):

| | `fy0_end` | months left | `w` | FY0 EPS | FY1 EPS | **NTM EPS** |
|---|---|---|---|---|---|---|
| MSFT | 2026-06-30 | 0.0 | **0.00** | 16.82 | 19.36 | **19.36** (pure FY1) |
| AMZN | 2026-12-31 | 5.6 | **0.46** | 8.66 | 9.88 | **9.32** |
| MRVL | 2027-01-31 | 6.6 | **0.55** | 4.05 | 6.18 | **5.01** |

MSFT's `w = 0` makes NTM EPS = FY1 = $19.36 → `price/NTM_eps` = **20.4x**, exactly `forward_pe`. **The
15% contradiction self-resolves at the root** — no gate required.

### 2.2 Ingestion (`saturn/ingestion/consensus.py`)

- **`RawConsensus`**: replace `forward_revenue` / `forward_eps_ntm` (which held raw `0y` values — they
  are now *derived*, not raw) with the blend inputs:
  `eps_fy0`, `eps_fy1`, `rev_fy0`, `rev_fy1` (`float | None`), `fy0_end` (`date | None`).
- **`fetch_consensus`**: read **both** `0y` and `+1y` from `earnings_estimate` and `revenue_estimate`
  (same defensive try/except + NaN rejection as today), and `.info["nextFiscalYearEnd"]` →
  `fy0_end` via `datetime.utcfromtimestamp(...).date()`. Each read is independently best-effort.
- **Pure helpers** (module-level, testable):
  ```python
  def _ntm_weight(fy0_end: date | None, today: date) -> float | None:
      """FY0's share of the next twelve months; None when the fiscal-year end is unknown."""
      if fy0_end is None:
          return None
      months_left = max(0.0, (fy0_end - today).days / _DAYS_PER_MONTH)
      return min(1.0, months_left / 12.0)

  def _blend_ntm(w: float | None, v0: float | None, v1: float | None) -> float | None:
      """Fiscal-year-progress-weighted NTM value; None unless the weight and BOTH years are known."""
      if w is None or v0 is None or v1 is None:
          return None
      return w * v0 + (1.0 - w) * v1
  ```
  with `_DAYS_PER_MONTH = 30.44`.
- **`validate_consensus`** gains a keyword-only `today: date | None = None` (defaults to
  `date.today()`) purely for deterministic tests. It computes
  `w = _ntm_weight(raw.fy0_end, today)` and blends both figures, then applies the **existing**
  margin/growth consistency gate to the **blended** values (unchanged bands/messages). On success it
  sets `snap.forward_eps_ntm`, `snap.forward_revenue`, and `snap.ntm_weight = w`.
- **No blend possible** (missing `fy0_end`, or either year of a series) → that NTM figure stays `None`.
  Append a `rejected` note: `"NTM blend: unavailable (no fiscal-year-end or incomplete FY0/FY1 estimates)"`.

### 2.3 Data model (`saturn/models.py`)

`ConsensusSnapshot` — semantics change plus one new field:
- `forward_eps_ntm` — **now the blended NTM EPS** (was: the `0y` EPS).
- `forward_revenue` — **now the blended NTM revenue** (was: the `0y` revenue).
- `forward_eps` / `forward_pe` — **unchanged**: the conventional FY+1 figures from `.info`, kept as the
  labeled reference anchor.
- **New:** `ntm_weight: float | None = None  # FY0's share of the blend; for transparency in the anchor`

### 2.4 Anchor (`_resolve_anchor` in `saturn/agents/synthesist.py`)

Speak in NTM; keep the conventional multiple as a labeled reference. When `dossier.quote.price` and
`cons.forward_eps_ntm > 0` are available, `ntm_pe = price / forward_eps_ntm` becomes the **primary**
anchor metric:

```python
    metric, value, unit = "NTM P/E", ntm_pe, "x"
```
and the text leads with it:
```
Consensus: NTM P/E 27.4x (on blended NTM EPS $9.32; 46% current FY / 54% next FY),
FY+1 P/E 25.8x on FY+1 EPS $9.88, mean target $X (+Y% vs price), rating buy, N analysts.
```
The blend note (`46% current FY / 54% next FY`) is emitted only when `ntm_weight is not None`. When no
NTM EPS is available, the existing precedence is unchanged (forward P/E → forward EPS → mean target).

This also **makes the anchor's existing `period="NTM"` label true** — today it stamps `"NTM"` on an
FY+1-based value.

### 2.5 Driver (`saturn/analytics/driver.py`) — drop the FY+1 fallback

```python
    consensus_eps = consensus.forward_eps_ntm if consensus is not None else None
```
(removing `or consensus.forward_eps`). Falling back to the FY+1 EPS silently reintroduces the exact
"up to two years forward" bug slice 1 fixed. Without a fiscal-year date we cannot know the horizon, so
the honest outcome is **no consensus comparison at all**: the bridge shows Saturn's own EPS, and the
gap/waterfall/two-lens fields stay `None`. The reader still gets consensus context from the anchor's
labeled FY+1 P/E. This is a deliberate, visible regression on names lacking fiscal-date data.

## 3. Part 2 — waterfall attribution sign

Negate both legs so the identity matches the displayed gap:

```python
        gap_from_growth = rev_ttm * (g - consensus_growth) * margin / shares
        gap_from_margin = rev_ttm * (1 + consensus_growth) * (margin - consensus_margin) / shares
```

New identity: **`gap_from_growth + gap_from_margin == eps_gap`** (where `eps_gap = saturn_eps −
consensus_eps`), replacing today's `== −eps_gap`. Algebra: the original legs sum to `E_c − E_s`;
negating both yields `E_s − E_c = eps_gap`. Render is unchanged (labels are already sign-agnostic);
AMZN then reads `+$0.71 … +0.08 from growth · +0.63 from margin`.

## 4. Part 3 — prose base-return tolerance

`_PROSE_RETURN_TOL: 0.15 → 0.02` in `saturn/agents/synthesist.py`. It is shared by the
`prose_vs_computed` check and `align_prose_base_return`, so both tighten together: a stated figure may
differ from the computed base return only by rounding. AMZN's 10pp gap is corrected; `"roughly +3%"`
vs `+4%` (1pp) still passes untouched.

## 5. Testing

- **`_ntm_weight` (unit):** `fy0_end` already past → `0.0` (MSFT case); ~5.6 months left → ≈`0.46`
  (AMZN case); `fy0_end` `None` → `None`; a date >12 months out clamps to `1.0`.
- **`_blend_ntm` (unit):** `w=0` → `v1`; `w=1` → `v0`; `w=0.46, v0=8.66, v1=9.88` → ≈`9.32`; any of
  `w`/`v0`/`v1` `None` → `None`.
- **`fetch_consensus`:** with stubbed `earnings_estimate` / `revenue_estimate` frames carrying `0y` and
  `+1y`, and `.info["nextFiscalYearEnd"]`, all five raw fields populate; each source missing/raising
  independently leaves its field `None` without raising.
- **`validate_consensus`:** given FY0/FY1 + `fy0_end` + an injected `today`, `forward_eps_ntm` /
  `forward_revenue` equal the blend and `ntm_weight` is set; the existing margin/growth gate still
  rejects an inconsistent **blended** revenue; missing `fy0_end` → both stay `None` with the
  `"NTM blend: unavailable"` note.
- **Driver:** `forward_eps_ntm` present → used as `consensus_eps`; `forward_eps_ntm` `None` **but
  `forward_eps` present** → `consensus_eps is None` and `eps_gap` / waterfall / two-lens all `None`
  (proves the fallback is gone).
- **Waterfall sign:** `gap_from_growth + gap_from_margin == eps_gap` to 1e-6, for both a
  Saturn-above-consensus and a Saturn-below-consensus fixture (signs follow the gap direction).
- **Anchor:** with quote + NTM EPS → `metric == "NTM P/E"`, `value == price/NTM_eps`, text contains the
  blend note and the `FY+1 P/E` reference; without NTM EPS → existing forward-P/E behaviour unchanged.
- **Prose tolerance:** a prose `+12%` against a computed `+22%` is now corrected to `+22%`; `+3%`
  against `+4%` is untouched.
- **Live:** regenerate MSFT and AMZN. Expect MSFT `w=0` → NTM EPS $19.36, anchor NTM P/E 20.4x
  (== FY+1 P/E, no contradiction), gap vs Saturn $18.86 ≈ **−$0.50**; AMZN NTM EPS ≈ $9.32 vs Saturn
  $9.37 → gap ≈ **+$0.05** (in-line). Both prior sign-flips resolved.

## 6. Scope

- **Modify:** `saturn/ingestion/consensus.py` (raw fields, both-row fetch, `fy0_end`, blend helpers,
  validate), `saturn/models.py` (`ConsensusSnapshot.ntm_weight`), `saturn/agents/synthesist.py`
  (`_resolve_anchor` NTM P/E, `_PROSE_RETURN_TOL`), `saturn/analytics/driver.py` (drop the FY+1
  fallback, negate the waterfall legs); touched tests. **No render change** — the anchor text is built
  in `_resolve_anchor`, and the Driver Bridge labels are sign-agnostic.

## 7. Out of scope

- A `price/forward_pe ≈ consensus_eps` consistency gate — unnecessary once the anchor and driver share
  one NTM EPS; the contradiction becomes unrepresentable rather than detected.
- P1 "scenario table as single source of truth" (LLM-authored prices/multiples in prose), P2 Critic
  upgrades (narrative-vs-own-table checks, high-severity alpha blocking), P3 news filtering / period
  tagging / capex annualisation.
- Blending anything other than the consensus EPS and revenue (targets, ratings untouched).

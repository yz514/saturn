# FCF: net finance-lease principal — Design

**Date:** 2026-07-05
**Status:** Approved (brainstorm) → ready for implementation plan
**Author:** Saturn (Claude) + user

## 1. Goal

Correct our free-cash-flow definition so it nets out **finance-lease principal
payments**, matching how lease-heavy companies (META and the hyperscalers) actually
define FCF. Today `FCF = OperatingCashFlow − CapitalExpenditures`, which **overstates**
FCF for any company that acquires assets via finance leases.

Motivating case (from an external review of our reports): our META FY2025 `fcf` reads
**$46.109B**; Meta's own reported FCF is **$43.585B**. The $2.524B gap is exactly Meta's
finance-lease principal payments.

## 2. Why this is the correct fix (accounting)

Under ASC 842, acquiring an asset via a **finance lease is a non-cash transaction** — it
never appears in CapEx (`PaymentsToAcquirePropertyPlantAndEquipment`). The cash cost
instead flows over time as lease payments, **split**: the **interest** portion runs
through *operating* (already reduces OCF), and the **principal** portion runs through
*financing* (`FinanceLeasePrincipalPayments`). So `OCF − CapEx` misses the capex-like
outflow entirely — the asset was acquired with no CapEx line, and the principal repayment
is buried in financing. Subtracting finance-lease principal recovers it:

```
FCF = OperatingCashFlow − CapitalExpenditures − FinanceLeasePrincipalPayments
```

Tie-out: `115.800 − 69.691 − 2.524 = 43.585` = Meta's reported FCF, to the dollar.

**No double-count** (verified per path):
- Interest is already in OCF; we subtract only **principal**.
- **Operating-lease** payments run entirely through OCF — we do **not** touch them
  (subtracting them would double-count). Only **finance**-lease principal is netted.
- Finance-lease asset acquisition is non-cash → not in CapEx → no overlap.
- XBRL reports the payment as a positive magnitude; we subtract it (correct sign).

**Deliberate limitation (documented, not a bug):** we subtract principal *payments* (cash
repayments of existing leases) — the standard, cash-consistent definition. For a company
rapidly *growing* its finance-lease book, non-cash new-lease *additions* exceed
repayments, so this still slightly understates the capex-substitute economics. Using
additions would make FCF no longer a pure **cash** metric; principal payments is the right
choice and matches Meta's own definition.

## 3. Scope & design decisions

1. **One adjusted `fcf`, not a separate metric.** Redefine the single canonical FCF
   helper; the correction cascades automatically to every FCF-derived metric: `fcf`,
   `fcf_margin`, `fcf_conversion`, `fcf_growth_yoy`, `fcf_per_share`, `dividend_coverage`,
   `p_fcf`, and the reverse-DCF base. This is the *more correct* number, not an
   alternative view. Companies without finance leases are unchanged.
2. **Absent → 0.** Finance-lease principal is optional: when the fact is missing, treat it
   as 0. It must **never gate** FCF (a no-lease name like MU must compute FCF exactly as
   before). Include the lease fact in metric provenance inputs only when present.
3. **Single source of truth.** `saturn/analytics/forward.py::_fcf_at` is currently a
   byte-for-byte duplicate of `saturn/analytics/metrics.py::_fcf`, and forward.py already
   imports helpers from metrics.py. **Delete `_fcf_at`** and reuse the canonical
   `metrics._fcf` in both `compute_forward` and `_fcf_cagr_3y`, so the reverse-DCF base and
   the metrics can never drift apart. (This duplication is the exact drift risk the change
   targets.)
4. **New EDGAR concept** `FinanceLeasePrincipalPayments` (primary US-GAAP tag). It is a
   duration cash-flow item, so it parses annual + Q1 like OCF/CapEx. No alias needed for
   the motivating cases.
5. **Catalog + docs.** Update the FCF formula strings in `METRIC_CATALOG`
   (`fcf`, `fcf_margin`, `fcf_conversion`, `fcf_per_share`) and regenerate
   `docs/metrics.md`; the existing drift-guard test enforces the regen.

## 4. Edge cases

- **No finance leases** (fact absent): lease term = 0 → FCF identical to today.
- **Netting pushes FCF ≤ 0:** `compute_forward` already guards `fcf[0] <= 0` and returns
  no reverse-DCF metrics (no fabrication) — acceptable and unchanged.
- **Company with finance leases under a non-standard tag we don't capture:** FCF slightly
  overstated for that name — no worse than today, and out of scope. Alias can be added
  later if a real name needs it.

## 5. Verification (live, like the TTM fix)

- **META FY2025** `fcf`: **$46.109B → $43.585B** (matches Meta's reported FCF). `p_fcf`,
  `fcf_margin`, and the reverse-DCF fair value shift accordingly.
- **A no-finance-lease name (MU)**: `fcf` unchanged vs. `main`.

## 6. File structure

- **Modify:** `saturn/ingestion/edgar.py` (add `FinanceLeasePrincipalPayments` concept);
  `saturn/analytics/metrics.py` (`_fcf` nets lease principal, absent→0);
  `saturn/analytics/forward.py` (delete `_fcf_at`, import & use `metrics._fcf`);
  `saturn/analytics/catalog.py` (4 formula strings); `docs/metrics.md` (regenerated).
- **Test:** `tests/analytics/test_metrics.py` (FCF nets lease principal; absent→0 unchanged);
  `tests/analytics/test_forward.py` (reverse-DCF base uses adjusted FCF);
  `tests/ingestion/test_edgar.py` (new concept parses) if such coverage exists.

## 7. Out of scope

- Operating-lease treatment (correctly already fully in OCF).
- Non-cash finance-lease *additions* (would break the cash definition).
- Legacy/alternate finance-lease tags (add on demand).
- Any change to the reverse-DCF model itself (cyclical normalization is a separate item).

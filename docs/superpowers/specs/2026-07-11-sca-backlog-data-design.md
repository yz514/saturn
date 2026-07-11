# SCA / Backlog XBRL Data Layer — Design

**Date:** 2026-07-11
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Give the analyst (and the Critic) **structured, provenance-clean SCA-adjacent data** — the
contracted-backlog and customer-deposit figures — which are sitting in XBRL for free but we
don't yet pull. This is the deterministic first step toward the reviewer's "Strategic
Customer Agreements" depth, chosen over a paid/scraped transcript source.

## 2. What's available (confirmed in MU companyfacts)

Both are **instant** (point-in-time) concepts, so they parse as balance-sheet facts:
- `RevenueRemainingPerformanceObligation` (RPO / contracted backlog): MU **$5.0B** (2026-05-28), up from $229M (2021).
- `ContractWithCustomerLiability` (customer deposits / deferred revenue): MU **$422M**, up from $169M.

## 3. Honest scope boundary

This is the **GAAP** SCA data. Management's headline **"~$100B SCA minimum-commitment RPO"**
and **"$22B deposits"** (from the earnings call) are a *non-GAAP management measure* that
lives only in the transcript — **out of scope** here (deferred until a transcript source is
chosen). The report should present GAAP RPO ($5B) as "small but sharply growing contracted
backlog," and must NOT conflate it with the $100B figure.

## 4. Design

1. **Two new EDGAR concepts** (instant, USD):
   - `RemainingPerformanceObligation` → `["RevenueRemainingPerformanceObligation"]`
   - `ContractLiability` → `["ContractWithCustomerLiability"]`
   They auto-flow into the Financial Snapshot table AND the analyst context (FUNDAMENTALS
   block) with provenance; the Critic can now verify RPO/deposit claims. Multi-period values
   give the trend.
2. **One derived metric** `rpo_to_revenue` = `RemainingPerformanceObligation / Revenues`
   (annual only — instant RPO over annual revenue = revenue-visibility / backlog coverage).
   Added to the `_efficiency` annual block; catalog entry (fmt `x`) + regenerated
   `docs/metrics.md` (drift-guard enforced).
3. **Prompt nudge** (one clause in `ANALYSIS_SYSTEM`): when RPO / contract-liabilities are
   present, discuss them as revenue-visibility / customer-commitment signals, noting GAAP
   RPO excludes non-binding long-term supply commitments.

## 5. Verification (live)

MU: `RemainingPerformanceObligation` shows ~$5.0B and `ContractLiability` ~$422M in the
facts table with multi-period trend; `rpo_to_revenue` computes (~0.13x on FY2025 revenue).

## 6. Scope

- **Modify:** `saturn/ingestion/edgar.py` (2 concepts), `saturn/analytics/catalog.py` +
  `saturn/analytics/metrics.py` (`rpo_to_revenue`), `docs/metrics.md` (regen),
  `saturn/workflows/equity_research.py` (prompt clause).
- **Test:** `tests/ingestion/test_edgar.py` (concepts registered),
  `tests/analytics/test_metrics.py` (`rpo_to_revenue` computed, annual-only).

## 7. Out of scope

- The transcript narrative + the $100B/$22B management figures (needs a transcript source).
- RPO time-banding (current vs >1yr) and per-segment backlog.

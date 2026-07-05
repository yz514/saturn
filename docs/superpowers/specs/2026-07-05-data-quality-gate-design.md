# Data Quality Gate — Design

**Date:** 2026-07-05
**Status:** Approved (brainstorm) → ready for implementation plan
**Author:** Saturn (Claude) + user

## 1. Goal

Stop stale as-reported values from appearing in a current report. Two external
reviews flagged the same failure: a FY2026 MU report whose Financial Snapshot showed
`PropertyPlantAndEquipmentNet` from **FY2017–FY2019** and `InterestExpense` from
**FY2021–FY2023** — 2–6 years stale, badly undermining credibility.

## 2. Root cause (confirmed empirically on MU companyfacts)

Tag migration — the same class PR #11 handles via per-period alias-merge, but our
alias *lists* predate the ASC 842-era tags:

| concept | we capture up to | current value lives under | real FY2025 |
|---|---|---|---|
| `PropertyPlantAndEquipmentNet` | FY2019 ($28.2B) | `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization` | $46.6B |
| `InterestExpense` | FY2023 ($388M) | `InterestExpenseNonoperating` | $477M (FY2024 $562M) |

A programmatic sweep of all 30 core concepts in the MU dossier found **exactly these
two** stale; everything else (LongTermDebt, OCF, CapEx, …) is current at FY2025.

## 3. Design — two complementary parts

### (a) Root-cause fix: add the migrated tag aliases

Extend the alias lists so recent data is actually captured:
- `InterestExpense`: append `InterestExpenseNonoperating`
- `PropertyPlantAndEquipmentNet`: append
  `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization`

Alias **order = preference**: the merge does `annual.setdefault(fy, row)` (first tag in
the list wins each period), so the plain tag is kept where present and the migrated tag
fills only the periods it's missing. Companies still using the plain tag are unchanged.
This flows through to derived metrics too — e.g. `interest_coverage` will finally
compute at FY2025 instead of FY2023. The existing report recency cap (3 annual / 4
quarterly per concept) then drops the old rows from the table automatically.

**Caveat (honest):** the combined PP&E tag bundles finance-lease ROU assets with owned
PP&E — a slightly broader definition, but it is how MU now presents productive assets
(and arguably more correct for asset-base ratios). Noted in the concept comment.

### (b) Systemic guard: a staleness gate on the report's main table

Defense-in-depth for any tag migration we haven't aliased yet, on any company. In
`_select_report_facts`, compute the dossier's newest fiscal frame and **exclude a
concept's rows from the Financial Snapshot when it has no recent value**, listing it
instead under a **"Data Quality Warnings"** note. Guarantees a FY2017 value can never
again sit in a FY2026 table.

## 4. Staleness rule (precise)

Constants (module-level, tunable): `_STALE_ANNUAL_YEARS = 1`, `_STALE_QUARTERS = 2`.

- `newest_fy` = max annual fiscal year across all facts (None if no annual facts).
- `newest_q_ord` = max `fy*4 + quarter` across all quarterly facts (None if none).
- Per concept:
  - annual rows are **fresh** if `newest_fy is None` or the concept's latest annual year
    `>= newest_fy - _STALE_ANNUAL_YEARS`; else the annual block is dropped.
  - quarterly rows are **fresh** if `newest_q_ord is None` or the concept's latest quarter
    ordinal `>= newest_q_ord - _STALE_QUARTERS`; else the quarterly block is dropped.
  - fresh blocks are capped as today (3 annual / 4 quarterly) and kept.
  - if the concept ends up contributing **zero** rows (both blocks stale) it is added to
    the warnings list as `(concept, latest_available_period_label)` — e.g.
    `("PropertyPlantAndEquipmentNet", "FY2019")`.

`_select_report_facts(facts) -> tuple[list, list[tuple[str, str]]]` returns
`(kept_facts, stale_warnings)`. The one caller (`render`, §5) renders the table from
`kept_facts` and, when `stale_warnings` is non-empty, appends:

```
_Data Quality Warnings — excluded from the table (no recent value; likely an unmapped XBRL tag):_
- PropertyPlantAndEquipmentNet — latest available FY2019
```

## 5. Edge cases

- **After aliasing, MU has no stale concepts** → warnings section absent. The gate only
  fires for genuinely-uncovered tags (verified by temporarily removing an alias).
- **Concept fresh quarterly but stale annual** (or vice versa): keep the fresh block,
  drop the stale one, not flagged (it still contributes rows).
- **No annual facts at all** (`newest_fy is None`): annual gate is a no-op (nothing to
  gate); quarterly gate still applies. Mock dossier / thin fixtures stay valid.
- **Definition drift within a concept** (pre-2020 owned-only PP&E vs recent
  owned+ROU): the recency cap shows only recent rows, which are internally consistent.

## 6. Verification (live, like the TTM/FCF fixes)

- Re-run MU: PP&E shows **FY2023–FY2025 (~$46.6B)**, interest shows **FY2024–FY2025**;
  **no FY2017–FY2020 rows anywhere**; `interest_coverage` computes at recent periods.
- Temporarily drop one alias → that concept appears under **Data Quality Warnings**
  instead of polluting the main table (unit-tested, not just manual).

## 7. Scope

- **Modify:** `saturn/ingestion/edgar.py` (two alias-list additions + PP&E caveat
  comment); `saturn/reports/markdown_report.py` (`_select_report_facts` returns
  `(kept, warnings)`; `render` renders the warnings note).
- **Test:** `tests/ingestion/test_edgar.py` (migrated alias captured via synthetic
  companyfacts); `tests/reports/test_markdown_report.py` (stale concept excluded +
  listed in warnings; fresh concepts unaffected).

## 8. Out of scope

- Filtering stale facts out of the **LLM context** (the aliases already fix the real
  data the LLM sees for covered concepts; a context-level staleness filter is a
  reasonable later follow-on).
- Segment / Q3-cash-flow / recent-news / Critic items (#2–#6) — separate slices.
- A full per-company alias audit beyond the two confirmed migrations (add on demand).

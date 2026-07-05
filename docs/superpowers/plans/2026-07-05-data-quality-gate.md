# Data Quality Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recent as-reported values never lost to tag migration (add the migrated aliases), and stale values never shown in the report table (a staleness gate that routes them to a Data Quality Warnings note).

**Architecture:** (a) Extend two alias lists in `EDGAR_CONCEPTS`; the existing per-period `setdefault` merge + recency cap do the rest. (b) `_select_report_facts` gains a staleness gate and returns `(kept_facts, stale_warnings)`; `render` shows the warnings note.

**Tech Stack:** Python, pytest. Design: `docs/superpowers/specs/2026-07-05-data-quality-gate-design.md`.

---

### Task 1: Add migrated tag aliases (PP&E, InterestExpense)

**Files:**
- Modify: `saturn/ingestion/edgar.py:39,54` (alias lists + PP&E caveat comment)
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Write the failing test**

In `tests/ingestion/test_edgar.py`:
```python
def _cf_migrated_interest():
    # Plain InterestExpense stops at FY2023; recent years only under InterestExpenseNonoperating.
    def _fy(y, val):
        return {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": val, "fy": y, "fp": "FY",
                "form": "10-K", "filed": f"{y+1}-01-15"}
    return {"cik": 723125, "facts": {"us-gaap": {
        "InterestExpense": {"units": {"USD": [_fy(2022, 189_000_000), _fy(2023, 388_000_000)]}},
        "InterestExpenseNonoperating": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 999, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-15"},
            _fy(2024, 562_000_000), _fy(2025, 477_000_000)]}},
    }}}


def test_interest_expense_alias_captures_migrated_tag():
    f = _parse_companyfacts(_cf_migrated_interest(), max_years=4)
    ie = {x.fiscal_period: x.value for x in f.facts if x.concept == "InterestExpense"}
    assert ie.get("FY2025") == 477_000_000       # recovered from InterestExpenseNonoperating
    assert ie.get("FY2024") == 562_000_000
    assert ie.get("FY2023") == 388_000_000        # plain tag wins the overlap (not 999)


def test_ppe_and_interest_aliases_registered():
    from saturn.ingestion.edgar import EDGAR_CONCEPTS
    assert "InterestExpenseNonoperating" in EDGAR_CONCEPTS["InterestExpense"]["tags"]
    assert ("PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"
            in EDGAR_CONCEPTS["PropertyPlantAndEquipmentNet"]["tags"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py::test_interest_expense_alias_captures_migrated_tag tests/ingestion/test_edgar.py::test_ppe_and_interest_aliases_registered -q`
Expected: FAIL (FY2025 interest absent; tags not registered).

- [ ] **Step 3: Add the aliases**

In `saturn/ingestion/edgar.py`:
- `InterestExpense` line → `"tags": ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense", "InterestExpenseNonoperating"]`
- `PropertyPlantAndEquipmentNet` line → `"tags": ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"]`
  and add an inline comment: `# migrated tag bundles finance-lease ROU assets with owned PP&E (post-ASC842)`

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/ingestion/test_edgar.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/ingestion/edgar.py tests/ingestion/test_edgar.py
git commit -m "fix(edgar): alias migrated PP&E/interest-expense tags (ASC842-era)"
```

---

### Task 2: Staleness gate on the report table

**Files:**
- Modify: `saturn/reports/markdown_report.py:75` (`_select_report_facts`), `:131` (caller in `render`)
- Test: `tests/reports/test_markdown_report.py`

- [ ] **Step 1: Write the failing test**

In `tests/reports/test_markdown_report.py` (import `_select_report_facts` and `FinancialFact`, `Provenance`):
```python
def _fact(concept, period, value):
    return FinancialFact(concept=concept, value=value, unit="USD", fiscal_period=period,
                         provenance=Provenance(source="SEC EDGAR"))


def test_select_report_facts_excludes_stale_concept():
    facts = [
        _fact("Revenues", "FY2025", 100.0), _fact("Revenues", "FY2024", 90.0),
        _fact("Revenues", "Q3 FY2026", 30.0),
        _fact("PropertyPlantAndEquipmentNet", "FY2019", 28.0),   # 6y stale
        _fact("PropertyPlantAndEquipmentNet", "Q3 FY2020", 30.0),
    ]
    kept, warnings = _select_report_facts(facts)
    kept_concepts = {f.concept for f in kept}
    assert "Revenues" in kept_concepts
    assert "PropertyPlantAndEquipmentNet" not in kept_concepts
    assert any(c == "PropertyPlantAndEquipmentNet" for c, _ in warnings)


def test_select_report_facts_keeps_fresh_concepts_no_warnings():
    facts = [_fact("Revenues", "FY2025", 100.0), _fact("Revenues", "FY2024", 90.0)]
    kept, warnings = _select_report_facts(facts)
    assert {f.concept for f in kept} == {"Revenues"} and warnings == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/reports/test_markdown_report.py -q -k stale`
Expected: FAIL (`_select_report_facts` returns a list, not a tuple; ValueError on unpack).

- [ ] **Step 3: Implement the gate**

Add module constants near the other `_RPT_*`:
```python
_STALE_ANNUAL_YEARS = 1
_STALE_QUARTERS = 2
```
Rewrite `_select_report_facts` to compute the newest fiscal frame and gate per concept, returning `(kept, warnings)`:
```python
def _q_ord(period: str) -> int | None:
    fy, q = _quarter_sort_key(period)
    return fy * 4 + q if fy > 0 else None


def _select_report_facts(facts: list) -> tuple[list, list[tuple[str, str]]]:
    """Per concept, keep the most-recent annual/quarterly periods for the table, but
    EXCLUDE a concept entirely when it has no recent value (tag migration we don't map);
    excluded concepts are returned as (concept, latest_period) warnings."""
    by_concept: dict[str, list] = {}
    order: list[str] = []
    for f in facts:
        if f.concept not in by_concept:
            by_concept[f.concept] = []
            order.append(f.concept)
        by_concept[f.concept].append(f)

    annual_years = [_annual_sort_key(f.fiscal_period) for f in facts
                    if not (f.fiscal_period or "").startswith("Q") and _annual_sort_key(f.fiscal_period) > 0]
    q_ords = [_q_ord(f.fiscal_period) for f in facts if (f.fiscal_period or "").startswith("Q")]
    q_ords = [o for o in q_ords if o is not None]
    newest_fy = max(annual_years) if annual_years else None
    newest_q = max(q_ords) if q_ords else None

    annual_out: list = []
    quarterly_out: list = []
    warnings: list[tuple[str, str]] = []
    for concept in order:
        items = by_concept[concept]
        annual = sorted([x for x in items if not (x.fiscal_period or "").startswith("Q")],
                        key=lambda x: _annual_sort_key(x.fiscal_period), reverse=True)
        quarterly = sorted([x for x in items if (x.fiscal_period or "").startswith("Q")],
                           key=lambda x: _quarter_sort_key(x.fiscal_period), reverse=True)
        annual_fresh = (newest_fy is None or (annual and _annual_sort_key(annual[0].fiscal_period) >= newest_fy - _STALE_ANNUAL_YEARS))
        quarterly_fresh = (newest_q is None or (quarterly and _q_ord(quarterly[0].fiscal_period) >= newest_q - _STALE_QUARTERS))
        kept_annual = annual[:_RPT_MAX_ANNUAL] if annual_fresh else []
        kept_quarterly = quarterly[:_RPT_MAX_QUARTERS] if quarterly_fresh else []
        if not kept_annual and not kept_quarterly and items:
            latest = annual[0].fiscal_period if annual else (quarterly[0].fiscal_period if quarterly else "N/A")
            warnings.append((concept, latest))
            continue
        annual_out.extend(kept_annual)
        quarterly_out.extend(kept_quarterly)
    return annual_out + quarterly_out, warnings
```

- [ ] **Step 4: Update the caller in `render` (§5 Financial Snapshot)**

Replace the `for fact in _select_report_facts(...)` block so it unpacks the tuple and renders the warnings note:
```python
        selected, stale_warnings = _select_report_facts(c.fundamentals.facts)
        for fact in selected:
            ...  # unchanged row rendering
        out.append("")
        out.append(f"_Showing the most recent {_RPT_MAX_ANNUAL} annual and {_RPT_MAX_QUARTERS} quarterly periods per concept._")
        if stale_warnings:
            out.append("")
            out.append("_Data Quality Warnings — excluded from the table (no recent value; likely an unmapped XBRL tag):_")
            for concept, latest in stale_warnings:
                out.append(f"- {concept} — latest available {latest}")
        out.append("")
```

- [ ] **Step 5: Run report tests**

Run: `.venv/Scripts/python.exe -m pytest tests/reports/test_markdown_report.py -q`
Expected: PASS (fix any other call site that assumed the old return type).

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add saturn/reports/markdown_report.py tests/reports/test_markdown_report.py
git commit -m "feat(report): staleness gate routes stale concepts to Data Quality Warnings"
```

---

## Final verification (after both tasks)

Live, no LLM:
```bash
.venv/Scripts/python.exe -c "from saturn.ingestion.dossier import build_dossier; d=build_dossier('MU'); \
rows=[(f.concept,f.fiscal_period,f.value) for f in d.fundamentals.facts if f.concept in ('PropertyPlantAndEquipmentNet','InterestExpense')]; \
[print(r) for r in rows]"
```
Expect PP&E at FY2023–FY2025 (~$46.6B) and InterestExpense at FY2024–FY2025 (~$477M); no FY2017–FY2020. Then regenerate the MU report and confirm the Financial Snapshot has no stale rows and (with all aliases present) no Data Quality Warnings section.

Then dispatch a final holistic reviewer and finish the branch (PR to `main`).

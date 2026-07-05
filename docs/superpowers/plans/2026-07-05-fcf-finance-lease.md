# FCF Finance-Lease Correction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redefine free cash flow as `OCF − CapEx − FinanceLeasePrincipalPayments` from a single canonical helper, so every FCF-derived metric and the reverse-DCF match how lease-heavy companies (META) report FCF.

**Architecture:** One canonical `metrics._fcf` (absent lease → 0); delete the duplicate `forward._fcf_at` and reuse the canonical one so the reverse-DCF base can't drift. Add the `FinanceLeasePrincipalPayments` EDGAR concept. Update catalog formula strings + regenerate `docs/metrics.md`.

**Tech Stack:** Python, pytest. Design: `docs/superpowers/specs/2026-07-05-fcf-finance-lease-design.md`.

---

### Task 1: FCF nets finance-lease principal from one canonical source

**Files:**
- Modify: `saturn/ingestion/edgar.py` (add concept), `saturn/analytics/metrics.py:96` (`_fcf`), `saturn/analytics/forward.py:11,76,136,148` (delete `_fcf_at`, import & use `metrics._fcf`)
- Test: `tests/analytics/test_metrics.py`, `tests/analytics/test_forward.py`, `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Write failing tests (metrics)**

In `tests/analytics/test_metrics.py`:
```python
def test_fcf_nets_finance_lease_principal():
    # META FY2025: 115.800 - 69.691 - 2.524 = 43.585 (Meta's reported FCF)
    f = _facts([
        ("OperatingCashFlow", "FY2025", 115.800),
        ("CapitalExpenditures", "FY2025", 69.691),
        ("FinanceLeasePrincipalPayments", "FY2025", 2.524),
    ])
    fcf = _by_name(compute_metrics(f, None), "fcf", "FY2025")
    assert fcf is not None and abs(fcf.value - 43.585) < 1e-9


def test_fcf_unchanged_when_no_finance_lease():
    # Absent finance-lease fact -> treated as 0 -> FCF = OCF - CapEx, as before.
    f = _facts([
        ("OperatingCashFlow", "FY2025", 100.0),
        ("CapitalExpenditures", "FY2025", 30.0),
    ])
    fcf = _by_name(compute_metrics(f, None), "fcf", "FY2025")
    assert fcf is not None and abs(fcf.value - 70.0) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py::test_fcf_nets_finance_lease_principal -q`
Expected: FAIL (`fcf` value is 46.109, not 43.585 — lease not yet netted).

- [ ] **Step 3: Add the EDGAR concept**

In `saturn/ingestion/edgar.py`, inside the `# Cash flow (USD)` block of `EDGAR_CONCEPTS`, after `CapitalExpenditures`:
```python
    "FinanceLeasePrincipalPayments": {"unit": "USD", "tags": ["FinanceLeasePrincipalPayments"]},
```

- [ ] **Step 4: Redefine the canonical `_fcf`**

Replace `saturn/analytics/metrics.py::_fcf` (currently lines 96-101):
```python
def _fcf(idx, period) -> tuple[float, list[MetricInput]] | None:
    """Free cash flow = operating cash flow - capex - finance-lease principal payments.
    Finance-lease asset acquisitions are non-cash (never in capex) and their principal
    repayment sits in financing, so plain OCF-capex overstates FCF for lease-heavy names;
    netting the principal matches how such companies (e.g. META) report FCF. The lease
    term is optional: absent -> 0, so it never blocks FCF and no-lease names are unchanged."""
    ocf = _fact(idx, "OperatingCashFlow", period)
    capex = _fact(idx, "CapitalExpenditures", period)
    if not ocf or not capex:
        return None
    lease = _fact(idx, "FinanceLeasePrincipalPayments", period)
    lease_val = lease.value if lease else 0.0
    inputs = [_in(ocf), _in(capex)] + ([_in(lease)] if lease else [])
    return (ocf.value - capex.value - lease_val, inputs)
```

- [ ] **Step 5: Run metrics tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_metrics.py -q`
Expected: PASS (both new tests + existing FCF-family tests unchanged).

- [ ] **Step 6: Write failing test (forward reverse-DCF uses the adjusted base)**

In `tests/analytics/test_forward.py`:
```python
def test_reverse_dcf_base_nets_finance_lease_principal():
    # Adding a finance-lease principal fact lowers fcf0 -> lower reverse-DCF fair value,
    # proving the reverse-DCF consumes the same canonical (adjusted) FCF.
    q = _quote()
    without = {m.name: m.value for m in compute_forward(_ff(_positive_fcf_rows()), q)}
    withl = {m.name: m.value for m in compute_forward(
        _ff(_positive_fcf_rows() + [("FinanceLeasePrincipalPayments", "FY2025", 100.0)]), q)}
    assert withl["reverse_dcf_fair_value_per_share"] < without["reverse_dcf_fair_value_per_share"]
```

- [ ] **Step 7: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_forward.py::test_reverse_dcf_base_nets_finance_lease_principal -q`
Expected: FAIL (fair values equal — `_fcf_at` ignores the lease fact).

- [ ] **Step 8: Delete `_fcf_at`; reuse the canonical `_fcf`**

In `saturn/analytics/forward.py`:
- Update the import (line 11) to also bring in `_fcf`:
  ```python
  from saturn.analytics.metrics import _annual_periods, _fact, _fcf, _in, _index
  ```
- Delete the entire `_fcf_at` function (lines 76-82).
- In `_fcf_cagr_3y` and `compute_forward`, replace every `_fcf_at(` call with `_fcf(`.

- [ ] **Step 9: Run forward tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_forward.py -q`
Expected: PASS.

- [ ] **Step 10: Add EDGAR concept registration test**

In `tests/ingestion/test_edgar.py`:
```python
def test_finance_lease_principal_concept_registered():
    from saturn.ingestion.edgar import EDGAR_CONCEPTS
    assert "FinanceLeasePrincipalPayments" in EDGAR_CONCEPTS
    assert "FinanceLeasePrincipalPayments" in EDGAR_CONCEPTS["FinanceLeasePrincipalPayments"]["tags"]
```

- [ ] **Step 11: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all green; existing FCF-family tests still pass because no-lease fixtures net 0).

- [ ] **Step 12: Commit**

```bash
git add saturn/ingestion/edgar.py saturn/analytics/metrics.py saturn/analytics/forward.py tests/
git commit -m "fix(analytics): net finance-lease principal out of FCF (single canonical helper)"
```

---

### Task 2: Update catalog formula strings + regenerate docs

**Files:**
- Modify: `saturn/analytics/catalog.py` (formula strings for `fcf`, `fcf_margin`, `fcf_conversion`, `fcf_per_share`), `docs/metrics.md` (regenerated)
- Test: existing drift-guard test in `tests/analytics/test_catalog.py`

- [ ] **Step 1: Update the four FCF formula strings**

In `saturn/analytics/catalog.py`, change the formula text to reflect the netted definition:
- `fcf`: `"OperatingCashFlow - CapitalExpenditures - FinanceLeasePrincipalPayments"`
- `fcf_margin`: `"(OperatingCashFlow - CapitalExpenditures - FinanceLeasePrincipalPayments) / Revenues"`
- `fcf_conversion`: `"(OperatingCashFlow - CapitalExpenditures - FinanceLeasePrincipalPayments) / NetIncomeLoss"`
- `fcf_per_share`: `"(OperatingCashFlow - CapitalExpenditures - FinanceLeasePrincipalPayments) / WeightedAverageSharesDiluted"`

- [ ] **Step 2: Run the drift-guard test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_catalog.py -q -k drift`
Expected: FAIL (docs/metrics.md out of date vs catalog).

- [ ] **Step 3: Regenerate docs/metrics.md**

Run the project's docs-regeneration entrypoint (the one the drift test checks against), e.g.:
```bash
.venv/Scripts/python.exe -c "from saturn.analytics.catalog import render_metrics_reference, METRICS_DOC_PATH; METRICS_DOC_PATH.write_text(render_metrics_reference(), encoding='utf-8')"
```

- [ ] **Step 4: Run the drift-guard test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/analytics/test_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add saturn/analytics/catalog.py docs/metrics.md
git commit -m "docs(metrics): reflect finance-lease-netted FCF in catalog + metrics.md"
```

---

## Final verification (after both tasks)

Live checks (no LLM), like the TTM fix:
- **META FY2025** `fcf` ≈ **$43.585B** (was $46.109B); `p_fcf` / `fcf_margin` shift accordingly.
- **MU** (no finance leases) `fcf` **unchanged** vs. `main`.

Then dispatch a final holistic reviewer, and finish the branch (PR to `main`).

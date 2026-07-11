# SCA / Backlog XBRL Data Layer — Plan

> Small deterministic slice; execute inline with TDD. Design: `docs/superpowers/specs/2026-07-11-sca-backlog-data-design.md`.

### Task 1: Concepts + `rpo_to_revenue` metric

**Files:** `saturn/ingestion/edgar.py`, `saturn/analytics/catalog.py`, `saturn/analytics/metrics.py`, `docs/metrics.md`; tests in `tests/ingestion/test_edgar.py`, `tests/analytics/test_metrics.py`.

- [ ] **Failing tests:**
  - test_edgar: `assert "RemainingPerformanceObligation" in EDGAR_CONCEPTS` and `"ContractLiability" in EDGAR_CONCEPTS` (with the right tags).
  - test_metrics: synthetic facts `RemainingPerformanceObligation FY2025=5.0`, `Revenues FY2025=40.0` → `rpo_to_revenue` FY2025 == 0.125; and assert it is NOT emitted for a quarterly period (annual-only).
- [ ] **Implement:**
  - `edgar.py` EDGAR_CONCEPTS (add a "Backlog / contracts (USD)" group): `"RemainingPerformanceObligation": {"unit": "USD", "tags": ["RevenueRemainingPerformanceObligation"]},` and `"ContractLiability": {"unit": "USD", "tags": ["ContractWithCustomerLiability"]},`
  - `catalog.py`: `_d("rpo_to_revenue", "Efficiency", "x", "RemainingPerformanceObligation / Revenues", "Contracted backlog (RPO) as a multiple of annual revenue — revenue visibility.", "Annual only; GAAP RPO excludes non-binding long-term supply commitments (e.g. SCA minimums).")`
  - `metrics.py` `_efficiency`, inside the `if period.startswith("FY")` block: `out.append(_ratio(idx, period, "rpo_to_revenue", "RemainingPerformanceObligation", "Revenues"))`
  - Regenerate `docs/metrics.md` via the catalog render entrypoint.
- [ ] Run tests + drift-guard + full suite → green. Commit.

### Task 2: Prompt nudge

**Files:** `saturn/workflows/equity_research.py`.

- [ ] Add one clause to `ANALYSIS_SYSTEM`: "When RemainingPerformanceObligation (contracted backlog / RPO) or ContractLiability (customer deposits) appear, discuss them as revenue-visibility and customer-commitment signals — noting GAAP RPO excludes non-binding long-term supply commitments (e.g. strategic customer agreements)."
- [ ] Full suite → green. Commit.

## Verification (live)
`build_dossier('MU')`: RemainingPerformanceObligation ~$5.0B + ContractLiability ~$422M in facts (multi-period), `rpo_to_revenue` ~0.13x. Then PR to main.

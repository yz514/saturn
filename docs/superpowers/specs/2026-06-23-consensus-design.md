# yfinance Consensus Slice Design

**Date:** 2026-06-23
**Status:** Approved (brainstorm) → ready for implementation plan
**Author:** Saturn (Claude) + user

## 1. Goal

Add an **analyst-consensus / forward** view as a clearly-marked, best-effort
`"yfinance (estimate)"` data class — distinct from as-reported facts and from the
`Saturn (model)` reverse-DCF. **Every value is validated against our verified
as-reported baseline before it is surfaced**; anything implausible is dropped with a
transparent reason. Guiding rule: *never let a contaminated estimate reach the
analyst unflagged.*

Motivation: a live probe showed yfinance consensus can be badly contaminated — AVGO
forward EPS $19.35 vs its real trailing ~$4.8 (split-scrambled), and the estimate
tables systemically inflated (MSFT +34% / AAPL +22% / AVGO +170% revenue). The
**`.info` summary tier is clean for non-split names**; this slice ingests only that
tier and gates it.

## 2. Scope — the reliable summary tier

From yfinance `.info` plus `earnings_history`:

- `forward_eps`, `forward_pe`, `peg`
- price targets `target_mean` / `target_high` / `target_low` + `target_upside_pct`
- `rating` (recommendationKey) + `n_analysts` (numberOfAnalystOpinions)
- `last_eps_surprise_pct` (most recent quarter: actual vs estimate)

**Out of scope (deferred):** the `revenue_estimate` / `earnings_estimate` tables
(systemically inflated), any paid/licensed feed, and estimate-revision history.

## 3. Architecture — fetch vs validate

Clean split so the I/O is thin and the *logic* (the heart of the slice) is pure and
offline-testable.

- **`fetch_consensus(ticker) -> RawConsensus`** (`saturn/ingestion/consensus.py`) — a
  thin yfinance reader returning the raw fields above (no judgement). Routed through
  the soft-fail dispatcher (`route_to_source("consensus", ...)`) like quote/edgar/fred:
  any failure becomes a recorded gap, never a crash.
- **`validate_consensus(raw, fundamentals, quote) -> ConsensusSnapshot`** — a **pure
  function** that cross-checks each raw value against our verified EDGAR/quote baseline
  and emits only what passes, plus a `rejected` list of `(field, reason)`.
- Wired in `build_dossier` **after** EDGAR + quote (validation needs the verified
  trailing numbers): `dossier.consensus = validate_consensus(raw, dossier.fundamentals,
  dossier.quote)` when the consensus fetch succeeded.

`RawConsensus` is a plain dataclass/dict of optional raw values; `ConsensusSnapshot`
is the validated, typed result (see §5).

## 4. Validation gates (the centerpiece)

`validate_consensus` is pure and uses our **verified** baseline:
- `trailing_eps` = the latest fiscal-year `EarningsPerShareDiluted` fact from
  `fundamentals` (a verified as-reported value; "latest FY" = max `FY{year}` present).
  Chosen for simplicity and because it's always available when EDGAR succeeded; a
  trailing-twelve-month EPS is a possible later refinement but unnecessary given the
  wide validation band. If absent or `<= 0`, the EPS-dependent gates can't run (see
  below).
- `price` = `quote.price` (None → target/EPS gates that need price can't run).

Per field, **validate independently; drop + record a reason on failure** (granularity
matters — AVGO's targets were fine even though its EPS was split-broken):

| Field(s) | Gate | Rationale |
|---|---|---|
| `forward_eps`, `forward_pe`, `peg` | (a) **plausible vs verified trailing**: `forward_eps / trailing_eps - 1` within `[-0.60, +1.50]`; AND (b) **internal consistency**: `abs(forward_pe - price/forward_eps) / (price/forward_eps) <= 0.05`. Reject the trio if either fails (peg also rejected if forward_eps is rejected). Needs `trailing_eps > 0` and `forward_eps`, `price` present; otherwise reject (can't validate → don't surface). | Catches AVGO (+266% vs trailing → reject) and split/stale scaling. |
| `target_mean`, `target_high`, `target_low`, `target_upside_pct` | require `low <= mean <= high`, each within `[0.2*price, 5.0*price]`, and `n_analysts >= 3`. `target_upside_pct = target_mean/price - 1`. Drop the target group if any check fails or `target_mean`/`price` missing. | Keeps sane targets even when EPS is broken. |
| `rating`, `n_analysts` | surface only if `n_analysts >= 3` and `rating` present. | Avoids thin/no-coverage noise. |
| `last_eps_surprise_pct` | from `earnings_history` (most recent row with both actual & estimate): `(actual - estimate) / abs(estimate)`; reject if `abs(...) > 2.0` or `estimate == 0`. | Drops absurd/garbage surprise values. |

Thresholds are module constants in `consensus.py` (e.g. `EPS_GROWTH_BAND = (-0.60,
1.50)`, `TARGET_PRICE_BAND = (0.2, 5.0)`, `MIN_ANALYSTS = 3`, `MAX_SURPRISE = 2.0`,
`PE_CONSISTENCY_TOL = 0.05`), easy to tune.

Each rejection is recorded as a human-readable string, e.g.
`"forward_eps: rejected — implies +266% vs verified trailing 4.80 (outside [-60%, +150%])"`,
so the *absence* of a value is explained rather than silent.

## 5. Representation & provenance

A dedicated model (mirrors `MacroSnapshot` — a distinct ingested data class, NOT part
of `derived_metrics`):

```python
class ConsensusSnapshot(BaseModel):
    forward_eps: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    target_upside_pct: float | None = None
    rating: str | None = None
    n_analysts: int | None = None
    last_eps_surprise_pct: float | None = None
    provenance: Provenance              # source="yfinance (estimate)"
    rejected: list[str] = Field(default_factory=list)
```

On `CompanyDossier`: `consensus: ConsensusSnapshot | None = None`.

This is the **third epistemic class** alongside `"Saturn (derived)"` (deterministic
from our facts) and `"Saturn (model)"` (reverse-DCF). Its provenance source
`"yfinance (estimate)"` signals "external, best-effort, may be unreliable."

## 6. Integration

- **Report:** a new **"Consensus / Analyst Expectations (estimate)"** section after
  Recent News (the plan decides exact placement/numbering; prefer a sub-section to
  avoid renumbering churn). Renders the validated fields (forward P/E, PEG, target
  range + upside, rating, last surprise) with a prominent note: *"Best-effort analyst
  estimates from yfinance; values failing validation against as-reported data are
  dropped and listed below."* Followed by the `rejected` reasons when non-empty. If the
  snapshot is `None`, render `"_No analyst consensus available._"`.
- **Context:** a `CONSENSUS / ANALYST EXPECTATIONS (yfinance estimate; may be
  unreliable)` block so the analyst can cite it *with* the caveat and naturally
  contrast consensus forward P/E against our reverse-DCF implied growth. Rejected
  fields are noted so the LLM knows what was withheld and why.
- **`saturn doctor`:** add a consensus reachability check (`check_consensus`) — pings
  `fetch_consensus` for the test ticker; `[OK]` if it returns at least one field,
  `[WARN]`/`[FAIL]` otherwise. Never raises.

## 7. Edge cases

- **No coverage** (small/foreign names): raw fields all `None` → snapshot has only
  provenance (or `dossier.consensus = None`), recorded as a gap.
- **Missing verified baseline** (EDGAR gap → no trailing EPS): skip the EPS-dependent
  validations and **reject** `forward_eps`/`forward_pe`/`peg` (can't validate → don't
  surface); targets/rating/surprise can still pass (they don't need the baseline).
- **yfinance shape change / exception:** `fetch_consensus` catches and returns empty;
  the dispatcher records a gap.
- **Negative trailing EPS** (loss-maker): EPS-growth band undefined → reject the EPS
  trio (don't surface a forward P/E off a negative base).

## 8. Testing (validation is the focus)

`tests/ingestion/test_consensus.py` — heavy offline unit tests on `validate_consensus`
with synthetic `RawConsensus` + verified baseline:
- **AVGO split case:** forward_eps far above trailing → EPS trio rejected (with reason),
  price targets retained.
- **Clean case:** all fields plausible → all surfaced, `rejected` empty.
- **Internal inconsistency:** `forward_pe` not equal to `price/forward_eps` → trio
  rejected.
- **Target out of band / `high<low` / too few analysts:** target group dropped.
- **Absurd surprise:** `|surprise| > 2.0` → dropped.
- **Missing baseline:** EPS trio rejected, targets still evaluated.
- **Negative trailing EPS:** EPS trio rejected.
- Threshold boundaries (just inside/outside each band).

Plus: thin monkeypatched `fetch_consensus` test (shape mapping only); a `build_dossier`
integration test (consensus attached, validated, real + mock); `doctor` check test;
report + context rendering tests (section present, rejection list shown, `None` path).

## 9. Out of scope (named follow-ons)

- The `revenue_estimate` / `earnings_estimate` tables (next-FY consensus revenue/EPS,
  forward P/S, consensus growth) — deferred unless validation proves them salvageable.
- A paid/licensed estimates feed (higher reliability).
- Estimate-revision trends (up/down over time).
- Mapping consensus into a buy/sell signal — out of scope here and project-wide (the
  Critic/decision layer is Phase 1).

## 10. File structure summary

- **Create:** `saturn/ingestion/consensus.py` (`fetch_consensus`, `validate_consensus`,
  thresholds, `RawConsensus`); `tests/ingestion/test_consensus.py`
- **Modify:** `saturn/models.py` (`ConsensusSnapshot`, `CompanyDossier.consensus`);
  `saturn/ingestion/dossier.py` (route + validate + attach, real + mock);
  `saturn/reports/markdown_report.py` (Consensus section); `saturn/workflows/
  equity_research.py` (context block); `saturn/diagnostics.py` + `saturn/cli.py`
  (`check_consensus`); touched report/context/doctor/dossier tests.

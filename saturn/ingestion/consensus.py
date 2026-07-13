"""yfinance analyst-consensus adapter: thin fetch + a pure validator that gates every
value against our verified as-reported baseline. Best-effort 'estimate' provenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import yfinance as yf  # noqa: E402  (kept module-level so tests can patch saturn.ingestion.consensus.yf)

from saturn.analytics.metrics import _annual_periods, _fact, _index, _ttm_or_fy
from saturn.models import ConsensusSnapshot, Fundamentals, Provenance, Quote

logger = logging.getLogger(__name__)

# Validation thresholds (tunable in one place).
EPS_GROWTH_BAND = (-0.60, 1.50)   # forward EPS vs verified trailing EPS
TARGET_PRICE_BAND = (0.2, 5.0)    # price targets as a multiple of current price
MIN_ANALYSTS = 3
MAX_SURPRISE = 2.0                 # |last EPS surprise|
PE_CONSISTENCY_TOL = 0.05         # |forward_pe - price/forward_eps| / (price/forward_eps)
REVENUE_MARGIN_CAP = 0.6          # implied consensus net margin must be below this
REVENUE_GROWTH_BAND = (-0.5, 1.0)  # implied consensus revenue growth must be within this

_SOURCE = "yfinance (estimate)"


@dataclass
class RawConsensus:
    """Unvalidated raw fields read from yfinance."""
    forward_eps: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    rating: str | None = None
    n_analysts: int | None = None
    last_actual_eps: float | None = None
    last_estimate_eps: float | None = None
    forward_revenue: float | None = None
    forward_eps_ntm: float | None = None


def _latest_fy_eps(fundamentals: Fundamentals | None) -> float | None:
    if not fundamentals:
        return None
    rows = []
    for f in fundamentals.facts:
        p = f.fiscal_period or ""
        if f.concept == "EarningsPerShareDiluted" and p.startswith("FY") and f.value is not None:
            try:
                rows.append((int(p[2:]), f.value))
            except ValueError:
                continue
    return max(rows, key=lambda t: t[0])[1] if rows else None


def _quarterly_eps_desc(fundamentals: Fundamentals | None) -> list[float]:
    """Single-quarter diluted EPS values, most-recent first."""
    rows = []
    for f in (fundamentals.facts if fundamentals else []):
        p = f.fiscal_period or ""
        if f.concept == "EarningsPerShareDiluted" and p.startswith("Q") and f.value is not None:
            parts = p.split()
            if len(parts) == 2 and parts[1].startswith("FY"):
                try:
                    rows.append(((int(parts[1][2:]), int(parts[0][1:])), f.value))
                except ValueError:
                    continue
    rows.sort(key=lambda t: t[0], reverse=True)
    return [v for _k, v in rows]


def _trailing_eps_baseline(fundamentals: Fundamentals | None) -> float | None:
    """Current earnings-power baseline for validating a forward EPS: the larger of TTM
    diluted EPS and the annualized most-recent single-quarter EPS (run-rate). Judging a
    forward estimate against the *current run-rate* rather than a stale full fiscal year
    lets a genuine fast-grower's forward pass while still rejecting a contaminated or
    split-discontinuous value. Falls back to latest-FY EPS when no quarterly data."""
    qs = _quarterly_eps_desc(fundamentals)
    candidates: list[float] = []
    if len(qs) >= 4:
        candidates.append(sum(qs[:4]))     # trailing twelve months
    if qs:
        candidates.append(qs[0] * 4)        # annualized latest quarter (run-rate)
    if candidates:
        return max(candidates)
    return _latest_fy_eps(fundamentals)


def validate_consensus(
    raw: RawConsensus, fundamentals: Fundamentals | None, quote: Quote | None
) -> ConsensusSnapshot:
    """Gate each raw consensus field against the verified baseline; surface only what
    passes, recording a human-readable reason for each rejection."""
    rejected: list[str] = []
    snap = ConsensusSnapshot(provenance=Provenance(source=_SOURCE, as_of=date.today(), retrieved_at=date.today()))
    price = quote.price if quote else None
    trailing_eps = _trailing_eps_baseline(fundamentals)

    # --- forward EPS / forward PE / PEG ---
    fe = raw.forward_eps
    if fe is not None:
        if price is None or price <= 0:
            rejected.append("forward_eps: no usable price to validate against")
        elif trailing_eps is None or trailing_eps <= 0:
            rejected.append(f"forward_eps: no positive current run-rate EPS to validate against (got {trailing_eps})")
        else:
            growth = fe / trailing_eps - 1
            lo, hi = EPS_GROWTH_BAND
            implied_pe = price / fe if fe else None
            inconsistent = (
                raw.forward_pe is not None and implied_pe
                and abs(raw.forward_pe - implied_pe) > PE_CONSISTENCY_TOL * implied_pe
            )
            if not (lo <= growth <= hi):
                rejected.append(
                    f"forward_eps/forward_pe/peg: rejected — forward_eps implies {growth:+.0%} vs current run-rate EPS "
                    f"{trailing_eps:.2f} (outside [{lo:+.0%}, {hi:+.0%}])"
                )
            elif inconsistent:
                rejected.append(
                    f"forward_eps/forward_pe/peg: rejected — forward_pe {raw.forward_pe} inconsistent with price/forward_eps {implied_pe:.1f}"
                )
            else:
                snap.forward_eps = fe
                snap.forward_pe = raw.forward_pe if raw.forward_pe is not None else implied_pe
                snap.peg = raw.peg

    # --- forward revenue + current-FY EPS (consistency gate: implied margin & growth must be sane) ---
    # The revenue ("0y" row) and EPS (forward_eps_ntm, "0y" row) are a horizon-matched pair — both
    # ~1 year forward of TTM — so the gate validates them together and feeds them to the driver as a
    # coherent NTM consensus. They are accepted or rejected as a unit.
    fr = raw.forward_revenue
    ntm_eps = raw.forward_eps_ntm
    if fr is not None:
        idx = _index(fundamentals)
        annual = _annual_periods(idx)
        ttm = _ttm_or_fy(idx, "Revenues")
        shares_fact = _fact(idx, "WeightedAverageSharesDiluted", annual[0]) if annual else None
        if ntm_eps and ntm_eps > 0 and ttm and ttm[0] > 0 and shares_fact and shares_fact.value > 0 and fr > 0:
            m_c = ntm_eps * shares_fact.value / fr
            g_c = fr / ttm[0] - 1
            lo, hi = REVENUE_GROWTH_BAND
            if 0 < m_c < REVENUE_MARGIN_CAP and lo <= g_c <= hi:
                snap.forward_revenue = fr
                snap.forward_eps_ntm = ntm_eps
            else:
                rejected.append(f"forward_revenue: rejected — implies margin {m_c:.0%} / growth {g_c:+.0%}")
        else:
            rejected.append("forward_revenue: no baseline (shares/revenue/current-FY EPS) to validate")

    # --- price targets ---
    tm, th, tl, na = raw.target_mean, raw.target_high, raw.target_low, raw.n_analysts
    if tm is not None:
        if price is None or price <= 0:
            rejected.append("price_target: no usable price to validate against")
        else:
            lo, hi = TARGET_PRICE_BAND

            def _in_band(v):
                return v is None or (lo * price <= v <= hi * price)

            ordered = (tl is None or th is None) or (tl <= tm <= th)
            if not (_in_band(tm) and _in_band(th) and _in_band(tl)):
                rejected.append(f"price_target: rejected — outside [{lo}x, {hi}x] of price {price}")
            elif not ordered:
                rejected.append("price_target: rejected — low/mean/high not ordered")
            elif na is None or na < MIN_ANALYSTS:
                rejected.append(f"price_target: rejected — only {na} analysts (< {MIN_ANALYSTS})")
            else:
                snap.target_mean, snap.target_high, snap.target_low = tm, th, tl
                snap.target_upside_pct = tm / price - 1

    # --- rating ---
    if raw.rating:
        if raw.n_analysts is not None and raw.n_analysts >= MIN_ANALYSTS:
            snap.rating = raw.rating
            snap.n_analysts = raw.n_analysts
        else:
            rejected.append(f"rating: withheld — only {raw.n_analysts} analysts (< {MIN_ANALYSTS})")

    # --- last EPS surprise ---
    a, e = raw.last_actual_eps, raw.last_estimate_eps
    if a is not None and e is not None:
        if e == 0:
            rejected.append("eps_surprise: rejected — estimate is zero")
        else:
            surprise = (a - e) / abs(e)
            if abs(surprise) <= MAX_SURPRISE:
                snap.last_eps_surprise_pct = surprise
            else:
                rejected.append(f"eps_surprise: rejected — {surprise:+.0%} implausible")

    snap.rejected = rejected
    return snap


def fetch_consensus(ticker: str) -> RawConsensus:
    """Read the reliable .info summary fields + last earnings surprise from yfinance.
    Thin and defensive: returns whatever is present; never raises on a missing field."""
    handle = yf.Ticker(ticker)
    info = handle.info or {}
    raw = RawConsensus(
        forward_eps=info.get("forwardEps"),
        forward_pe=info.get("forwardPE"),
        peg=info.get("pegRatio") if info.get("pegRatio") is not None else info.get("trailingPegRatio"),
        target_mean=info.get("targetMeanPrice"),
        target_high=info.get("targetHighPrice"),
        target_low=info.get("targetLowPrice"),
        rating=info.get("recommendationKey"),
        n_analysts=info.get("numberOfAnalystOpinions"),
    )
    # last earnings surprise (best-effort; column names vary across yfinance versions)
    try:
        hist = handle.earnings_history
        if hist is not None and len(hist) and "epsActual" in hist.columns and "epsEstimate" in hist.columns:
            row = hist.dropna(subset=["epsActual", "epsEstimate"]).tail(1)
            if len(row):
                raw.last_actual_eps = float(row["epsActual"].iloc[0])
                raw.last_estimate_eps = float(row["epsEstimate"].iloc[0])
    except Exception as exc:  # noqa: BLE001 - surprise is optional
        logger.debug("consensus earnings_history unavailable for %s: %s", ticker, exc)
    # Forward revenue + current-FY EPS estimates (best-effort; the analysis tables are flaky across
    # yfinance versions). We read the "0y" (current fiscal year) rows so both figures sit ~1 year
    # forward of TTM — matching the driver model's NTM bridge. (".info forwardEps" is the "+1y"/next-FY
    # number; it stays as the forward-P/E anchor, a different horizon and a different purpose.)
    try:
        est = handle.revenue_estimate
        if est is not None and "avg" in getattr(est, "columns", []) and "0y" in getattr(est, "index", []):
            v = est.loc["0y", "avg"]
            if v is not None and float(v) == float(v):   # reject NaN
                raw.forward_revenue = float(v)
    except Exception as exc:  # noqa: BLE001 - revenue estimate is optional
        logger.debug("consensus revenue_estimate unavailable for %s: %s", ticker, exc)
    try:
        ee = handle.earnings_estimate
        if ee is not None and "avg" in getattr(ee, "columns", []) and "0y" in getattr(ee, "index", []):
            v = ee.loc["0y", "avg"]
            if v is not None and float(v) == float(v):   # reject NaN
                raw.forward_eps_ntm = float(v)
    except Exception as exc:  # noqa: BLE001 - earnings estimate is optional
        logger.debug("consensus earnings_estimate unavailable for %s: %s", ticker, exc)
    return raw

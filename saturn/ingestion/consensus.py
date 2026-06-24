"""yfinance analyst-consensus adapter: thin fetch + a pure validator that gates every
value against our verified as-reported baseline. Best-effort 'estimate' provenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from saturn.models import ConsensusSnapshot, Fundamentals, Provenance, Quote

logger = logging.getLogger(__name__)

# Validation thresholds (tunable in one place).
EPS_GROWTH_BAND = (-0.60, 1.50)   # forward EPS vs verified trailing EPS
TARGET_PRICE_BAND = (0.2, 5.0)    # price targets as a multiple of current price
MIN_ANALYSTS = 3
MAX_SURPRISE = 2.0                 # |last EPS surprise|
PE_CONSISTENCY_TOL = 0.05         # |forward_pe - price/forward_eps| / (price/forward_eps)

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


def validate_consensus(
    raw: RawConsensus, fundamentals: Fundamentals | None, quote: Quote | None
) -> ConsensusSnapshot:
    """Gate each raw consensus field against the verified baseline; surface only what
    passes, recording a human-readable reason for each rejection."""
    rejected: list[str] = []
    snap = ConsensusSnapshot(provenance=Provenance(source=_SOURCE, as_of=date.today(), retrieved_at=date.today()))
    price = quote.price if quote else None
    trailing_eps = _latest_fy_eps(fundamentals)

    # --- forward EPS / forward PE / PEG ---
    fe = raw.forward_eps
    if fe is not None:
        if price is None:
            rejected.append("forward_eps: no price to validate against")
        elif trailing_eps is None or trailing_eps <= 0:
            rejected.append(f"forward_eps: no positive verified trailing EPS to validate against (got {trailing_eps})")
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
                    f"forward_eps: rejected — implies {growth:+.0%} vs verified trailing "
                    f"{trailing_eps:.2f} (outside [{lo:+.0%}, {hi:+.0%}])"
                )
            elif inconsistent:
                rejected.append(
                    f"forward_pe: rejected — {raw.forward_pe} inconsistent with price/forward_eps {implied_pe:.1f}"
                )
            else:
                snap.forward_eps = fe
                snap.forward_pe = raw.forward_pe if raw.forward_pe is not None else implied_pe
                snap.peg = raw.peg

    # --- price targets ---
    tm, th, tl, na = raw.target_mean, raw.target_high, raw.target_low, raw.n_analysts
    if tm is not None:
        if price is None:
            rejected.append("price_target: no price to validate against")
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
    if a is not None and e:
        surprise = (a - e) / abs(e)
        if abs(surprise) <= MAX_SURPRISE:
            snap.last_eps_surprise_pct = surprise
        else:
            rejected.append(f"eps_surprise: rejected — {surprise:+.0%} implausible")

    snap.rejected = rejected
    return snap

"""Driver model: a deterministic trailing-trend forward-EPS bridge + consensus decomposition.

Pure and offline; derived only from as-reported financials (+ consensus forward EPS). The base
case is a MECHANICAL trailing-trend baseline (backward-looking), NOT a forward judgment — its
value is transparency (a number whose math you can see) and attribution (why it differs from the
Street), not superior forecasting.
"""
from __future__ import annotations

from datetime import date

from saturn.analytics.metrics import _annual_periods, _fact, _index, _ttm_or_fy
from saturn.models import DriverModel, Provenance

_MODEL = "Saturn (model)"
_EXTREME_GROWTH = 0.60   # a consensus-implied growth beyond this is flagged low-confidence


def _revenue_cagr_3y(idx, latest_fy: str) -> float | None:
    cur = _fact(idx, "Revenues", latest_fy)
    prev = _fact(idx, "Revenues", f"FY{int(latest_fy[2:]) - 3}")
    if not cur or not prev or cur.value <= 0 or prev.value <= 0:
        return None
    return (cur.value / prev.value) ** (1 / 3) - 1


def compute_driver_model(fundamentals, quote, consensus, *, growth_override: float | None = None) -> DriverModel | None:
    """Trailing-trend forward EPS + consensus two-lens decomposition. Soft-returns None when a
    required input is missing. `quote` is reserved for the FCF bridge / P-E cross-check in later
    slices (unused here). Consensus fields populate only when a consensus forward EPS exists."""
    idx = _index(fundamentals)
    annual = _annual_periods(idx)
    if not annual:
        return None
    latest_fy = annual[0]
    rev = _ttm_or_fy(idx, "Revenues")
    ni = _ttm_or_fy(idx, "NetIncomeLoss")
    shares_fact = _fact(idx, "WeightedAverageSharesDiluted", latest_fy)
    if rev is None or ni is None or shares_fact is None or rev[0] <= 0 or shares_fact.value <= 0:
        return None
    rev_ttm, ni_ttm, shares = rev[0], ni[0], shares_fact.value
    margin = ni_ttm / rev_ttm

    caveats: list[str] = []
    low_conf = False
    if growth_override is not None:
        g = growth_override
        growth_source = "guidance"
    else:
        growth_source = "trend"
        g = _revenue_cagr_3y(idx, latest_fy)
        if g is None:
            g = 0.0
            caveats.append("no 3-year revenue history; growth assumed 0%")
            low_conf = True
    if margin <= 0:
        caveats.append("trailing net margin is non-positive; the trend-EPS bridge is unreliable")
        low_conf = True

    saturn_eps = rev_ttm * (1 + g) * margin / shares

    consensus_eps = consensus.forward_eps if consensus is not None else None
    eps_gap = eps_gap_pct = implied_g = implied_m = None
    if consensus_eps:
        eps_gap = saturn_eps - consensus_eps
        eps_gap_pct = eps_gap / abs(consensus_eps)
        if margin > 0:
            implied_g = (consensus_eps * shares / margin) / rev_ttm - 1        # Lens A
        implied_m = consensus_eps * shares / (rev_ttm * (1 + g))               # Lens B
        if implied_g is not None and abs(implied_g) > _EXTREME_GROWTH:
            caveats.append(f"consensus implies revenue growth of {implied_g:.0%} — extreme vs trend")
            low_conf = True

    return DriverModel(
        horizon="NTM",
        saturn_eps=saturn_eps,
        trailing_revenue_growth=g,
        trailing_net_margin=margin,
        shares=shares,
        consensus_eps=consensus_eps,
        eps_gap=eps_gap,
        eps_gap_pct=eps_gap_pct,
        consensus_implied_growth=implied_g,
        consensus_implied_margin=implied_m,
        low_confidence=low_conf,
        caveats=caveats,
        growth_source=growth_source,
        provenance=Provenance(source=_MODEL, as_of=date.today()),
    )

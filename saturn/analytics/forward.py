"""Forward / expectations metrics via a 2-stage reverse-DCF on verified levered FCF.

Pure and offline; derived only from price + as-reported financials (no estimates).
"""

from __future__ import annotations

from datetime import date

from saturn.analytics.catalog import METRIC_CATALOG
from saturn.analytics.metrics import _annual_periods, _fact, _fcf, _in, _index
from saturn.models import DerivedMetric, Fundamentals, MetricInput, Provenance, Quote

HORIZON_YEARS = 10
TERMINAL_GROWTH = 0.025
DISCOUNT_RATES = (0.08, 0.10, 0.12)   # low, base/mid, high
BASE_DISCOUNT = 0.10
GROWTH_CAP = 0.25                      # caps the fair-value growth assumption only
SOLVER_G_BOUNDS = (-0.50, 0.60)        # search range for the implied-growth solver
SOLVER_R_BOUNDS = (0.00, 0.50)         # search range for the implied-return solver

_MODEL = "Saturn (model)"
_ASSUMPTION = "Saturn (model assumption)"


def _dcf(fcf0: float, g: float, r: float, *, n: int = HORIZON_YEARS, g_t: float = TERMINAL_GROWTH) -> float:
    """Present value of a 2-stage FCF stream: grow at g for n years, then terminal
    growth g_t, all discounted at r. Requires r > g_t."""
    pv = 0.0
    fcf = fcf0
    for t in range(1, n + 1):
        fcf = fcf * (1 + g)
        pv += fcf / (1 + r) ** t
    terminal = fcf * (1 + g_t) / (r - g_t)   # fcf is FCF_n after the loop
    pv += terminal / (1 + r) ** n
    return pv


def _bisect(func, lo: float, hi: float, target: float, *, tol: float = 1e-7, iters: int = 200) -> float | None:
    """Find x in [lo, hi] with func(x) ~= target for a MONOTONIC func (either
    direction). Returns None if target is outside [func(lo), func(hi)]."""
    flo, fhi = func(lo), func(hi)
    if target < min(flo, fhi) or target > max(flo, fhi):
        return None
    increasing = flo < fhi
    for _ in range(iters):
        mid = (lo + hi) / 2
        fmid = func(mid)
        if abs(fmid - target) <= tol * max(1.0, abs(target)):
            return mid
        if (fmid < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _solve_implied_growth(fcf0: float, target: float, r: float) -> tuple[float, bool]:
    """The Stage-1 growth the price implies. Returns (g, converged); on out-of-range
    clamps to the nearer search bound with converged=False."""
    lo, hi = SOLVER_G_BOUNDS
    g = _bisect(lambda x: _dcf(fcf0, x, r), lo, hi, target)
    if g is not None:
        return (g, True)
    return (hi, False) if target > _dcf(fcf0, hi, r) else (lo, False)


def _solve_implied_return(fcf0: float, g: float, target: float) -> float | None:
    """The discount rate that equates DCF(g, r) to target, or None if out of range.
    Lower bound is held above the terminal growth (the terminal formula needs r > g_t)."""
    lo = max(SOLVER_R_BOUNDS[0], TERMINAL_GROWTH + 1e-4)
    hi = SOLVER_R_BOUNDS[1]
    return _bisect(lambda x: _dcf(fcf0, g, x), lo, hi, target)


def _fcf_cagr_3y(idx, latest_fy: str) -> float | None:
    cur = _fcf(idx, latest_fy)
    prev = _fcf(idx, f"FY{int(latest_fy[2:]) - 3}")
    if not cur or not prev or cur[0] <= 0 or prev[0] <= 0:
        return None
    return (cur[0] / prev[0]) ** (1 / 3) - 1


def _assume(concept: str, value: float) -> MetricInput:
    return MetricInput(concept=concept, fiscal_period=None, value=value, source=_ASSUMPTION)


def _fmetric(name: str, value: float | None, inputs: list[MetricInput]) -> DerivedMetric | None:
    if value is None:
        return None
    d = METRIC_CATALOG[name]
    return DerivedMetric(
        name=name, value=value, format=d.fmt, fiscal_period="model",
        formula=d.formula, inputs=inputs,
        provenance=Provenance(source=_MODEL, as_of=date.today()),
    )


def compute_forward(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
    if quote is None or quote.market_cap is None:
        return []
    idx = _index(fundamentals)
    annual = _annual_periods(idx)
    if not annual:
        return []
    latest_fy = annual[0]
    fcf = _fcf(idx, latest_fy)
    if not fcf or fcf[0] <= 0:
        return []   # model meaningless for non-positive FCF — no fabrication
    fcf0 = fcf[0]
    mc = quote.market_cap
    mci = MetricInput(concept="market_cap", fiscal_period=None, value=mc, source=quote.provenance.source)
    base_assumptions = [
        _assume("discount_rate", BASE_DISCOUNT),
        _assume("terminal_growth", TERMINAL_GROWTH),
        _assume("horizon_years", float(HORIZON_YEARS)),
    ]
    out: list[DerivedMetric | None] = []

    g_imp, converged = _solve_implied_growth(fcf0, mc, BASE_DISCOUNT)
    imp_inputs = fcf[1] + [mci] + base_assumptions
    if not converged:
        # price implies growth beyond the search bounds; flag the clamp so a reader/LLM
        # can distinguish a genuine solve from a boundary hit (value is clamped, not bogus).
        imp_inputs.append(_assume("implied_growth_clamped_to_bound", 1.0))
    out.append(_fmetric("implied_fcf_growth", g_imp, imp_inputs))

    cagr = _fcf_cagr_3y(idx, latest_fy)
    if cagr is not None:
        g_fv = min(max(cagr, TERMINAL_GROWTH), GROWTH_CAP)
        cagr_in = _assume("trailing_3y_fcf_cagr", cagr)
        g_fv_in = _assume("growth_assumption", g_fv)
        out.append(_fmetric("expectations_gap", g_imp - cagr, fcf[1] + [mci] + base_assumptions + [cagr_in]))
        out.append(_fmetric("implied_return", _solve_implied_return(fcf0, g_fv, mc), fcf[1] + [mci, g_fv_in, cagr_in]))
        shares = _fact(idx, "WeightedAverageSharesDiluted", latest_fy)
        if shares and shares.value:
            low_r, mid_r, high_r = DISCOUNT_RATES   # 0.08, 0.10, 0.12
            sh = shares.value
            base_sh = fcf[1] + [_in(shares), g_fv_in, cagr_in]
            out.append(_fmetric("reverse_dcf_fair_value_per_share", _dcf(fcf0, g_fv, mid_r) / sh, base_sh + [_assume("discount_rate", mid_r)]))
            out.append(_fmetric("reverse_dcf_value_low_per_share", _dcf(fcf0, g_fv, high_r) / sh, base_sh + [_assume("discount_rate", high_r)]))
            out.append(_fmetric("reverse_dcf_value_high_per_share", _dcf(fcf0, g_fv, low_r) / sh, base_sh + [_assume("discount_rate", low_r)]))
            out.append(_fmetric("margin_of_safety", _dcf(fcf0, g_fv, mid_r) / mc - 1, fcf[1] + [mci, g_fv_in, cagr_in, _assume("discount_rate", mid_r)]))
    return [m for m in out if m]

"""Forward / expectations metrics via a 2-stage reverse-DCF on verified levered FCF.

Pure and offline; derived only from price + as-reported financials (no estimates).
"""

from __future__ import annotations

from datetime import date

from saturn.analytics.catalog import METRIC_CATALOG
from saturn.analytics.metrics import _annual_periods, _fact, _in, _index
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

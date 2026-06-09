"""Deterministic derived-metric computation over the as-reported dossier.

Pure and offline. Each metric's format/formula come from METRIC_CATALOG so the
number, the report, and docs/metrics.md never disagree.
"""

from __future__ import annotations

from datetime import date

from saturn.analytics.catalog import METRIC_CATALOG
from saturn.models import (
    DerivedMetric,
    FinancialFact,
    Fundamentals,
    MetricInput,
    Provenance,
    Quote,
)

# ----- shared helpers --------------------------------------------------------


def _index(fundamentals: Fundamentals | None) -> dict[tuple[str, str], FinancialFact]:
    out: dict[tuple[str, str], FinancialFact] = {}
    if fundamentals:
        for f in fundamentals.facts:
            if f.fiscal_period is not None and f.value is not None:
                out[(f.concept, f.fiscal_period)] = f
    return out


def _fact(idx, concept: str, period: str) -> FinancialFact | None:
    return idx.get((concept, period))


def _in(fact: FinancialFact) -> MetricInput:
    return MetricInput(
        concept=fact.concept,
        fiscal_period=fact.fiscal_period,
        value=fact.value,
        source=fact.provenance.source,
    )


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _make(name: str, value: float | None, period: str | None, inputs: list[MetricInput]) -> DerivedMetric | None:
    if value is None:
        return None
    d = METRIC_CATALOG[name]
    return DerivedMetric(
        name=name,
        value=value,
        format=d.fmt,
        fiscal_period=period,
        formula=d.formula,
        inputs=inputs,
        provenance=Provenance(source="Saturn (derived)", as_of=date.today()),
    )


def _ratio(idx, period, name, num_concept, den_concept) -> DerivedMetric | None:
    a = _fact(idx, num_concept, period)
    b = _fact(idx, den_concept, period)
    if not a or not b:
        return None
    return _make(name, _div(a.value, b.value), period, [_in(a), _in(b)])


def _annual_periods(idx) -> list[str]:
    ps = {p for (_c, p) in idx if p.startswith("FY")}
    return sorted(ps, key=lambda p: int(p[2:]), reverse=True)


def _quarterly_periods(idx) -> list[str]:
    ps = {p for (_c, p) in idx if p.startswith("Q")}

    def key(p: str) -> tuple[int, int] | None:
        parts = p.split()
        if len(parts) != 2 or not parts[1].startswith("FY"):
            return None
        try:
            return (int(parts[1][2:]), int(parts[0][1:]))
        except ValueError:
            return None

    keyed = [(key(p), p) for p in ps]
    return [p for k, p in sorted((kp for kp in keyed if kp[0] is not None), reverse=True)]


def _fcf(idx, period) -> tuple[float, list[MetricInput]] | None:
    ocf = _fact(idx, "OperatingCashFlow", period)
    capex = _fact(idx, "CapitalExpenditures", period)
    if not ocf or not capex:
        return None
    return (ocf.value - capex.value, [_in(ocf), _in(capex)])


# ----- metric families -------------------------------------------------------


def _profitability(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "gross_margin", "GrossProfit", "Revenues"),
        _ratio(idx, period, "operating_margin", "OperatingIncomeLoss", "Revenues"),
        _ratio(idx, period, "net_margin", "NetIncomeLoss", "Revenues"),
    ]
    rev = _fact(idx, "Revenues", period)
    oi = _fact(idx, "OperatingIncomeLoss", period)
    da = _fact(idx, "DepreciationAndAmortization", period)
    if rev and oi and da:
        out.append(_make("ebitda_margin", _div(oi.value + da.value, rev.value), period, [_in(oi), _in(da), _in(rev)]))
    fcf = _fcf(idx, period)
    if rev and fcf:
        out.append(_make("fcf_margin", _div(fcf[0], rev.value), period, fcf[1] + [_in(rev)]))
    return out


def _effective_tax_rate_value(idx, period) -> tuple[float, list[MetricInput]] | None:
    ni = _fact(idx, "NetIncomeLoss", period)
    tax = _fact(idx, "IncomeTaxExpenseBenefit", period)
    if not ni or not tax:
        return None
    pretax = ni.value + tax.value
    v = _div(tax.value, pretax)
    if v is None:
        return None
    return (v, [_in(tax), _in(ni)])


def _returns(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "roe", "NetIncomeLoss", "StockholdersEquity"),
        _ratio(idx, period, "roa", "NetIncomeLoss", "Assets"),
    ]
    assets = _fact(idx, "Assets", period)
    lc = _fact(idx, "LiabilitiesCurrent", period)
    oi = _fact(idx, "OperatingIncomeLoss", period)
    if oi and assets and lc:
        out.append(_make("roce", _div(oi.value, assets.value - lc.value), period, [_in(oi), _in(assets), _in(lc)]))
    etr = _effective_tax_rate_value(idx, period)
    if etr:
        out.append(_make("effective_tax_rate", etr[0], period, etr[1]))
    eq = _fact(idx, "StockholdersEquity", period)
    ltd = _fact(idx, "LongTermDebt", period)
    if oi and etr and eq and ltd:
        dc = _fact(idx, "DebtCurrent", period)
        total_debt = ltd.value + (dc.value if dc else 0.0)
        nopat = oi.value * (1 - etr[0])
        inputs = [_in(oi), _in(eq), _in(ltd)] + ([_in(dc)] if dc else [])
        out.append(_make("roic", _div(nopat, total_debt + eq.value), period, inputs))
    return out


# ----- entry point -----------------------------------------------------------


def compute_metrics(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
    idx = _index(fundamentals)
    out: list[DerivedMetric | None] = []
    for period in _annual_periods(idx) + _quarterly_periods(idx):
        out += _profitability(idx, period)
        out += _returns(idx, period)
    return [m for m in out if m]

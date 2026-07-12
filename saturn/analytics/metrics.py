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
    # roe/roa/roce/roic divide a period FLOW (earnings/operating income) by a
    # point-in-time STOCK (equity/assets/capital); only meaningful annually — a
    # single quarter's flow would understate the ratio ~4x.
    if not period.startswith("FY"):
        return []
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
    eq = _fact(idx, "StockholdersEquity", period)
    ltd = _fact(idx, "LongTermDebt", period)
    if oi and etr and eq and ltd:
        dc = _fact(idx, "DebtCurrent", period)
        total_debt = ltd.value + (dc.value if dc else 0.0)
        nopat = oi.value * (1 - etr[0])
        inputs = [_in(oi), _in(eq), _in(ltd)] + ([_in(dc)] if dc else [])
        out.append(_make("roic", _div(nopat, total_debt + eq.value), period, inputs))
    return out


def _total_debt(idx, period) -> tuple[float, list[MetricInput]] | None:
    ltd = _fact(idx, "LongTermDebt", period)
    if not ltd:
        return None
    dc = _fact(idx, "DebtCurrent", period)
    total = ltd.value + (dc.value if dc else 0.0)
    return (total, [_in(ltd)] + ([_in(dc)] if dc else []))


def _ebitda(idx, period) -> tuple[float, list[MetricInput]] | None:
    oi = _fact(idx, "OperatingIncomeLoss", period)
    da = _fact(idx, "DepreciationAndAmortization", period)
    if not oi or not da:
        return None
    return (oi.value + da.value, [_in(oi), _in(da)])


def _liquidity(idx, period) -> list[DerivedMetric | None]:
    out = [
        _ratio(idx, period, "current_ratio", "AssetsCurrent", "LiabilitiesCurrent"),
        _ratio(idx, period, "cash_ratio", "CashAndCashEquivalents", "LiabilitiesCurrent"),
    ]
    ac = _fact(idx, "AssetsCurrent", period)
    inv = _fact(idx, "Inventory", period)
    lc = _fact(idx, "LiabilitiesCurrent", period)
    if ac and inv and lc:
        out.append(_make("quick_ratio", _div(ac.value - inv.value, lc.value), period, [_in(ac), _in(inv), _in(lc)]))
    return out


def _leverage(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    td = _total_debt(idx, period)
    eq = _fact(idx, "StockholdersEquity", period)
    assets = _fact(idx, "Assets", period)
    cash = _fact(idx, "CashAndCashEquivalents", period)
    if td and eq:
        out.append(_make("debt_to_equity", _div(td[0], eq.value), period, td[1] + [_in(eq)]))
    if td and assets:
        out.append(_make("debt_to_assets", _div(td[0], assets.value), period, td[1] + [_in(assets)]))
    if td and cash:
        out.append(_make("net_debt", td[0] - cash.value, period, td[1] + [_in(cash)]))
        # net_debt is a point-in-time stock; EBITDA is a period flow -> annual only.
        if period.startswith("FY"):
            ebitda = _ebitda(idx, period)
            if ebitda:
                out.append(_make("net_debt_to_ebitda", _div(td[0] - cash.value, ebitda[0]), period, td[1] + [_in(cash)] + ebitda[1]))
    out.append(_ratio(idx, period, "interest_coverage", "OperatingIncomeLoss", "InterestExpense"))
    return out


def _efficiency(idx, period) -> list[DerivedMetric | None]:
    # capex_intensity is flow/flow -> valid any period.
    out: list[DerivedMetric | None] = [
        _ratio(idx, period, "capex_intensity", "CapitalExpenditures", "Revenues"),
    ]
    # Turnover ratios divide a period FLOW by a point-in-time STOCK, and DSO is an
    # annual figure (x365): all annual-only.
    if period.startswith("FY"):
        out.append(_ratio(idx, period, "asset_turnover", "Revenues", "Assets"))
        out.append(_ratio(idx, period, "inventory_turnover", "CostOfRevenue", "Inventory"))
        ar = _fact(idx, "AccountsReceivableNetCurrent", period)
        rev = _fact(idx, "Revenues", period)
        if ar and rev and rev.value != 0:
            out.append(_make("days_sales_outstanding", ar.value / rev.value * 365, period, [_in(ar), _in(rev)]))
    return out


def _cash(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    fcf = _fcf(idx, period)
    if fcf:
        out.append(_make("fcf", fcf[0], period, fcf[1]))
        ni = _fact(idx, "NetIncomeLoss", period)
        if ni:
            out.append(_make("fcf_conversion", _div(fcf[0], ni.value), period, fcf[1] + [_in(ni)]))
    return out


def _gr(a: float | None, b: float | None) -> float | None:
    """Growth ratio a/b - 1, or None when b is missing/zero. Handles a == 0."""
    if a is None or b is None or b == 0:
        return None
    return a / b - 1


def _prev_fy(period: str, back: int = 1) -> str:
    return f"FY{int(period[2:]) - back}"


def _prev_quarter(period: str) -> str:
    q, fy = period.split()
    n, y = int(q[1]), int(fy[2:])
    return f"Q4 FY{y - 1}" if n == 1 else f"Q{n - 1} FY{y}"


def _split_suspected(idx, period, prev_period) -> bool:
    """True when the diluted share count between two periods changed by a split-like
    factor (>2x or <0.5x). Stock splits leave companyfacts mixing split-adjusted and
    unadjusted per-share/share values across the tag history, so per-share growth and
    share-count change across that boundary are unreliable and should be skipped."""
    a = _fact(idx, "WeightedAverageSharesDiluted", period)
    b = _fact(idx, "WeightedAverageSharesDiluted", prev_period)
    if not a or not b or b.value == 0:
        return False
    ratio = a.value / b.value
    return ratio > 2.0 or ratio < 0.5


def _yoy(idx, period, name, concept, *, guard_shares: bool = False) -> DerivedMetric | None:
    if period.startswith("FY"):
        prev = _prev_fy(period)
    else:
        q, fy = period.split()
        prev = f"{q} FY{int(fy[2:]) - 1}"   # same quarter, prior year
    if guard_shares and _split_suspected(idx, period, prev):
        return None
    a = _fact(idx, concept, period)
    b = _fact(idx, concept, prev)
    if not a or not b:
        return None
    return _make(name, _gr(a.value, b.value), period, [_in(a), _in(b)])


def _cagr(idx, period, name, concept, years=3, *, guard_shares: bool = False) -> DerivedMetric | None:
    if not period.startswith("FY"):
        return None
    prev = _prev_fy(period, years)
    if guard_shares and _split_suspected(idx, period, prev):
        return None
    a = _fact(idx, concept, period)
    b = _fact(idx, concept, prev)
    if not a or not b or a.value <= 0 or b.value <= 0:
        return None
    return _make(name, (a.value / b.value) ** (1 / years) - 1, period, [_in(a), _in(b)])


def _growth(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = [
        _yoy(idx, period, "revenue_growth_yoy", "Revenues"),
        _yoy(idx, period, "eps_growth_yoy", "EarningsPerShareDiluted", guard_shares=True),
        _cagr(idx, period, "revenue_cagr_3y", "Revenues"),
        _cagr(idx, period, "eps_cagr_3y", "EarningsPerShareDiluted", guard_shares=True),
    ]
    # fcf_growth_yoy (annual): needs fcf at period and prior FY
    if period.startswith("FY"):
        cur = _fcf(idx, period)
        prev = _fcf(idx, _prev_fy(period))
        if cur and prev and prev[0] != 0:
            out.append(_make("fcf_growth_yoy", cur[0] / prev[0] - 1, period, cur[1] + prev[1]))
    # qoq (quarterly only)
    if period.startswith("Q"):
        a = _fact(idx, "Revenues", period)
        b = _fact(idx, "Revenues", _prev_quarter(period))
        if a and b:
            out.append(_make("revenue_growth_qoq", _gr(a.value, b.value), period, [_in(a), _in(b)]))
    return out


def _per_share(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    sh = _fact(idx, "WeightedAverageSharesDiluted", period)
    if not sh:
        return out
    fcf = _fcf(idx, period)
    if fcf:
        out.append(_make("fcf_per_share", _div(fcf[0], sh.value), period, fcf[1] + [_in(sh)]))
    eq = _fact(idx, "StockholdersEquity", period)
    if eq:
        out.append(_make("book_value_per_share", _div(eq.value, sh.value), period, [_in(eq), _in(sh)]))
    return out


def _quality(idx, period) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    etr = _effective_tax_rate_value(idx, period)
    if etr:
        out.append(_make("effective_tax_rate", etr[0], period, etr[1]))
    if period.startswith("FY"):
        prev = _prev_fy(period)
        a = _fact(idx, "WeightedAverageSharesDiluted", period)
        b = _fact(idx, "WeightedAverageSharesDiluted", prev)
        if a and b and not _split_suspected(idx, period, prev):
            out.append(_make("share_count_change_yoy", _gr(a.value, b.value), period, [_in(a), _in(b)]))
    fcf = _fcf(idx, period)
    div = _fact(idx, "DividendsPaid", period)
    if fcf and div:
        out.append(_make("dividend_coverage", _div(fcf[0], div.value), period, fcf[1] + [_in(div)]))
    ni = _fact(idx, "NetIncomeLoss", period)
    ocf = _fact(idx, "OperatingCashFlow", period)
    assets = _fact(idx, "Assets", period)
    if ni and ocf and assets:
        out.append(_make("accruals_ratio", _div(ni.value - ocf.value, assets.value), period, [_in(ni), _in(ocf), _in(assets)]))
    return out


def _ttm(idx, concept) -> tuple[float, list[MetricInput]] | None:
    """Trailing-twelve-month total for a flow concept, correct across the Q4 gap
    (companies never file a standalone Q4 10-Q, so the four most-recent single quarters
    span more than a year). TTM = latest full FY + current-year YTD - prior-year YTD
    through the same quarter. Returns None (never a wrong sum-of-4) when the year is
    mid-stream and the aligning pieces aren't available."""
    quarters = [p for p in _quarterly_periods(idx) if _fact(idx, concept, p) is not None]
    if not quarters:
        return None
    cur_year = int(quarters[0].split()[1][2:])
    cur_qnums = sorted(int(p.split()[0][1:]) for p in quarters if int(p.split()[1][2:]) == cur_year)

    # Year already closed -> the reported annual IS the trailing-twelve-month figure.
    cur_annual = _fact(idx, concept, f"FY{cur_year}")
    if cur_annual is not None:
        return (cur_annual.value, [_in(cur_annual)])

    def _q_facts(year: int, nums: list[int]):
        fs = [_fact(idx, concept, f"Q{n} FY{year}") for n in nums]
        return fs if all(f is not None for f in fs) else None

    # A full four quarters of the current year -> sum them directly.
    if cur_qnums == [1, 2, 3, 4]:
        fs = _q_facts(cur_year, cur_qnums)
        return (sum(f.value for f in fs), [_in(f) for f in fs]) if fs else None

    # Partial year -> bridge off the prior full FY (needs contiguous Q1..Qk in both years).
    if cur_qnums != list(range(1, len(cur_qnums) + 1)):
        return None
    prior_annual = _fact(idx, concept, f"FY{cur_year - 1}")
    cur_fs = _q_facts(cur_year, cur_qnums)
    prior_fs = _q_facts(cur_year - 1, cur_qnums)
    if prior_annual is None or cur_fs is None or prior_fs is None:
        return None
    ttm = prior_annual.value + sum(f.value for f in cur_fs) - sum(f.value for f in prior_fs)
    inputs = [_in(prior_annual)] + [_in(f) for f in cur_fs] + [_in(f) for f in prior_fs]
    return (ttm, inputs)


def _ttm_or_fy(idx, concept) -> tuple[float, str, list[MetricInput]] | None:
    t = _ttm(idx, concept)
    if t:
        return (t[0], "TTM", t[1])
    fy = _annual_periods(idx)
    if fy:
        f = _fact(idx, concept, fy[0])
        if f:
            return (f.value, fy[0], [_in(f)])
    return None


def _mcap_input(quote: Quote) -> MetricInput:
    return MetricInput(concept="market_cap", fiscal_period=None, value=quote.market_cap, source=quote.provenance.source)


def _ttm_metrics(idx) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = []
    for name, concept in (("revenue_ttm", "Revenues"), ("net_income_ttm", "NetIncomeLoss"), ("eps_ttm", "EarningsPerShareDiluted")):
        t = _ttm(idx, concept)
        if t:
            out.append(_make(name, t[0], "TTM", t[1]))
    return out


def _valuation(idx, quote: Quote | None) -> list[DerivedMetric | None]:
    out: list[DerivedMetric | None] = list(_ttm_metrics(idx))
    if quote is None or quote.market_cap is None:
        return out
    mc = quote.market_cap
    mci = _mcap_input(quote)
    fy = _annual_periods(idx)
    latest_fy = fy[0] if fy else None

    # price multiples driven by TTM (else latest FY)
    ni = _ttm_or_fy(idx, "NetIncomeLoss")
    if ni:
        out.append(_make("pe_ratio", _div(mc, ni[0]), ni[1], [mci] + ni[2]))
        out.append(_make("earnings_yield", _div(ni[0], mc), ni[1], ni[2] + [mci]))
    rev = _ttm_or_fy(idx, "Revenues")
    if rev:
        out.append(_make("ps_ratio", _div(mc, rev[0]), rev[1], [mci] + rev[2]))
    eq = _fact(idx, "StockholdersEquity", latest_fy) if latest_fy else None
    if eq:
        out.append(_make("pb_ratio", _div(mc, eq.value), latest_fy, [mci, _in(eq)]))

    # FCF / EV multiples on latest FY
    if latest_fy:
        fcf = _fcf(idx, latest_fy)
        if fcf:
            out.append(_make("p_fcf", _div(mc, fcf[0]), latest_fy, [mci] + fcf[1]))
        td = _total_debt(idx, latest_fy)
        cash = _fact(idx, "CashAndCashEquivalents", latest_fy)
        ebitda = _ebitda(idx, latest_fy)
        if td and cash and ebitda:
            net_debt = td[0] - cash.value
            ev = mc + net_debt
            ev_inputs = [mci] + td[1] + [_in(cash)]
            out.append(_make("ev_ebitda", _div(ev, ebitda[0]), latest_fy, ev_inputs + ebitda[1]))
            if rev:
                out.append(_make("ev_sales", _div(ev, rev[0]), rev[1], ev_inputs + rev[2]))
        ni_fy = _fact(idx, "NetIncomeLoss", latest_fy)
        div = _fact(idx, "DividendsPaid", latest_fy)
        buyback = _fact(idx, "StockRepurchased", latest_fy)
        if div:
            out.append(_make("dividend_yield", _div(div.value, mc), latest_fy, [_in(div), mci]))
            if ni_fy:
                out.append(_make("payout_ratio", _div(div.value, ni_fy.value), latest_fy, [_in(div), _in(ni_fy)]))
        if buyback:
            out.append(_make("buyback_yield", _div(buyback.value, mc), latest_fy, [_in(buyback), mci]))
        if div and buyback:
            out.append(_make("total_shareholder_yield", _div(div.value + buyback.value, mc), latest_fy, [_in(div), _in(buyback), mci]))
    return out


# ----- entry point -----------------------------------------------------------


def _period_year(period: str | None) -> int | None:
    p = period or ""
    try:
        if p.startswith("FY"):
            return int(p[2:])
        if p.startswith("Q"):
            return int(p.split()[1][2:])
    except (ValueError, IndexError):
        return None
    return None


def _drop_stale(metrics: list[DerivedMetric], keep_years: int = 5) -> list[DerivedMetric]:
    """Drop metrics whose fiscal year is older than the latest minus keep_years, so a
    concept lacking recent data can't surface ancient periods as if current. TTM and
    point-in-time metrics (no FY/Q period) are always kept."""
    years = [y for m in metrics if (y := _period_year(m.fiscal_period)) is not None]
    if not years:
        return metrics
    cutoff = max(years) - keep_years + 1
    return [m for m in metrics if (y := _period_year(m.fiscal_period)) is None or y >= cutoff]


def _period_ordinal(period: str) -> tuple[int, int]:
    """Rank FY and Q periods on one timeline: 'FY2025'->(2025,4); 'Q3 FY2026'->(2026,3)."""
    if (period or "").startswith("FY"):
        try:
            return (int(period[2:]), 4)
        except ValueError:
            return (-1, -1)
    try:
        q, fy = period.split()
        return (int(fy[2:]), int(q[1]))
    except (ValueError, IndexError):
        return (-1, -1)


def _latest_fact(idx, concept):
    """(period, fact) for the most-recent period of `concept` across annual + quarterly."""
    facts = [(p, f) for (c, p), f in idx.items() if c == concept]
    return max(facts, key=lambda pf: _period_ordinal(pf[0])) if facts else None


def _backlog(idx) -> list[DerivedMetric | None]:
    """RPO coverage over two explicit revenue bases (explicit denominators avoid the ambiguity
    that let an LLM fabricate a mislabelled ratio). rpo_to_ttm_revenue uses trailing-12-mo
    revenue; rpo_to_annualized_quarterly_revenue uses the latest quarter annualized (current
    run-rate). Both need quarterly revenue, so annual-only filers emit neither."""
    latest = _latest_fact(idx, "RemainingPerformanceObligation")
    if not latest:
        return []
    period, rpo = latest
    out: list[DerivedMetric | None] = []
    ttm = _ttm(idx, "Revenues")
    if ttm is not None:
        ttm_val, ttm_inputs = ttm
        out.append(_make("rpo_to_ttm_revenue", _div(rpo.value, ttm_val), period, [_in(rpo), *ttm_inputs]))
    qps = _quarterly_periods(idx)
    if qps:
        rev_q = _fact(idx, "Revenues", qps[0])
        if rev_q is not None and rev_q.value:
            out.append(_make("rpo_to_annualized_quarterly_revenue",
                             _div(rpo.value, rev_q.value * 4), period, [_in(rpo), _in(rev_q)]))
    return out


def compute_metrics(fundamentals: Fundamentals | None, quote: Quote | None) -> list[DerivedMetric]:
    idx = _index(fundamentals)
    out: list[DerivedMetric | None] = []
    for period in _annual_periods(idx) + _quarterly_periods(idx):
        out += _profitability(idx, period)
        out += _returns(idx, period)
        out += _liquidity(idx, period)
        out += _leverage(idx, period)
        out += _efficiency(idx, period)
        out += _cash(idx, period)
        out += _growth(idx, period)
        out += _per_share(idx, period)
        out += _quality(idx, period)
    out += _valuation(idx, quote)
    out += _backlog(idx)
    return _drop_stale([m for m in out if m])

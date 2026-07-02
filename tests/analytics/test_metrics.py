from datetime import date

from saturn.analytics.metrics import compute_metrics
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _facts(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=PROV)
        for (c, p, v) in rows
    ])


def _by_name(metrics, name, period):
    return next((m for m in metrics if m.name == name and m.fiscal_period == period), None)


def test_profitability_margins_and_provenance():
    f = _facts([
        ("Revenues", "FY2025", 1000.0),
        ("GrossProfit", "FY2025", 600.0),
        ("OperatingIncomeLoss", "FY2025", 250.0),
        ("NetIncomeLoss", "FY2025", 200.0),
    ])
    ms = compute_metrics(f, None)
    gm = _by_name(ms, "gross_margin", "FY2025")
    assert gm is not None and abs(gm.value - 0.6) < 1e-9
    assert gm.format == "percent"                     # pulled from catalog
    assert gm.formula == "GrossProfit / Revenues"     # pulled from catalog
    assert gm.provenance.source == "Saturn (derived)"
    assert {i.concept for i in gm.inputs} == {"GrossProfit", "Revenues"}
    assert abs(_by_name(ms, "operating_margin", "FY2025").value - 0.25) < 1e-9
    assert abs(_by_name(ms, "net_margin", "FY2025").value - 0.20) < 1e-9


def test_zero_denominator_and_missing_input_skip():
    f = _facts([("GrossProfit", "FY2025", 600.0), ("Revenues", "FY2025", 0.0)])
    ms = compute_metrics(f, None)
    assert _by_name(ms, "gross_margin", "FY2025") is None   # zero revenue -> skipped
    f2 = _facts([("Revenues", "FY2025", 1000.0)])           # no GrossProfit
    assert _by_name(compute_metrics(f2, None), "gross_margin", "FY2025") is None
    # sanity: a valid metric is still produced alongside the skipped one
    f3 = _facts([("GrossProfit", "FY2025", 600.0), ("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", 200.0)])
    assert _by_name(compute_metrics(f3, None), "net_margin", "FY2025") is not None


def test_ebitda_and_fcf_margins():
    f = _facts([
        ("Revenues", "FY2025", 1000.0),
        ("OperatingIncomeLoss", "FY2025", 250.0),
        ("DepreciationAndAmortization", "FY2025", 100.0),
        ("OperatingCashFlow", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "ebitda_margin", "FY2025").value - 0.35) < 1e-9   # (250+100)/1000
    assert abs(_by_name(ms, "fcf_margin", "FY2025").value - 0.2) < 1e-9       # (300-100)/1000


def test_negative_value_passes_through():
    f = _facts([("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", -300.0)])
    nm = _by_name(compute_metrics(f, None), "net_margin", "FY2025")
    assert nm is not None and abs(nm.value - (-0.3)) < 1e-9


def test_returns_and_effective_tax_rate():
    f = _facts([
        ("NetIncomeLoss", "FY2025", 200.0),
        ("StockholdersEquity", "FY2025", 1000.0),
        ("Assets", "FY2025", 2500.0),
        ("LiabilitiesCurrent", "FY2025", 500.0),
        ("OperatingIncomeLoss", "FY2025", 300.0),
        ("IncomeTaxExpenseBenefit", "FY2025", 50.0),
        ("LongTermDebt", "FY2025", 400.0),
        ("DebtCurrent", "FY2025", 100.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "roe", "FY2025").value - 0.20) < 1e-9
    assert abs(_by_name(ms, "roa", "FY2025").value - 0.08) < 1e-9
    # effective tax rate = 50 / (200 + 50) = 0.20
    etr = _by_name(ms, "effective_tax_rate", "FY2025")
    assert abs(etr.value - 0.20) < 1e-9
    # roce = 300 / (2500 - 500) = 0.15
    assert abs(_by_name(ms, "roce", "FY2025").value - 0.15) < 1e-9
    # roic = (300 * (1 - 0.20)) / (500 + 1000) = 240 / 1500 = 0.16
    assert abs(_by_name(ms, "roic", "FY2025").value - 0.16) < 1e-9


def test_liquidity_and_leverage():
    f = _facts([
        ("AssetsCurrent", "FY2025", 2000.0),
        ("LiabilitiesCurrent", "FY2025", 1000.0),
        ("Inventory", "FY2025", 400.0),
        ("CashAndCashEquivalents", "FY2025", 300.0),
        ("LongTermDebt", "FY2025", 800.0),
        ("DebtCurrent", "FY2025", 200.0),
        ("StockholdersEquity", "FY2025", 2000.0),
        ("Assets", "FY2025", 5000.0),
        ("OperatingIncomeLoss", "FY2025", 500.0),
        ("DepreciationAndAmortization", "FY2025", 100.0),
        ("InterestExpense", "FY2025", 50.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "current_ratio", "FY2025").value - 2.0) < 1e-9
    assert abs(_by_name(ms, "quick_ratio", "FY2025").value - 1.6) < 1e-9      # (2000-400)/1000
    assert abs(_by_name(ms, "cash_ratio", "FY2025").value - 0.3) < 1e-9
    assert abs(_by_name(ms, "debt_to_equity", "FY2025").value - 0.5) < 1e-9   # 1000/2000
    assert abs(_by_name(ms, "debt_to_assets", "FY2025").value - 0.2) < 1e-9   # 1000/5000
    assert abs(_by_name(ms, "net_debt", "FY2025").value - 700.0) < 1e-9       # 1000-300
    # net_debt_to_ebitda = 700 / (500+100) = 1.1667
    assert abs(_by_name(ms, "net_debt_to_ebitda", "FY2025").value - (700.0 / 600.0)) < 1e-9
    assert abs(_by_name(ms, "interest_coverage", "FY2025").value - 10.0) < 1e-9


def test_efficiency_and_cash():
    f = _facts([
        ("Revenues", "FY2025", 1000.0),
        ("Assets", "FY2025", 2000.0),
        ("CostOfRevenue", "FY2025", 600.0),
        ("Inventory", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("AccountsReceivableNetCurrent", "FY2025", 200.0),
        ("OperatingCashFlow", "FY2025", 350.0),
        ("NetIncomeLoss", "FY2025", 250.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "asset_turnover", "FY2025").value - 0.5) < 1e-9
    assert abs(_by_name(ms, "inventory_turnover", "FY2025").value - 2.0) < 1e-9
    assert abs(_by_name(ms, "capex_intensity", "FY2025").value - 0.1) < 1e-9
    # dso = 200 / 1000 * 365 = 73
    assert abs(_by_name(ms, "days_sales_outstanding", "FY2025").value - 73.0) < 1e-9
    # fcf = 350 - 100 = 250
    assert abs(_by_name(ms, "fcf", "FY2025").value - 250.0) < 1e-9
    # fcf_conversion = 250 / 250 = 1.0
    assert abs(_by_name(ms, "fcf_conversion", "FY2025").value - 1.0) < 1e-9


def test_growth_yoy_cagr_qoq():
    f = _facts([
        ("Revenues", "FY2025", 1200.0),
        ("Revenues", "FY2024", 1000.0),
        ("Revenues", "FY2022", 600.0),
        ("EarningsPerShareDiluted", "FY2025", 5.0),
        ("EarningsPerShareDiluted", "FY2024", 4.0),
        ("OperatingCashFlow", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("OperatingCashFlow", "FY2024", 250.0),
        ("CapitalExpenditures", "FY2024", 100.0),
        ("Revenues", "Q2 FY2025", 320.0),
        ("Revenues", "Q1 FY2025", 300.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "revenue_growth_yoy", "FY2025").value - 0.2) < 1e-9
    assert abs(_by_name(ms, "eps_growth_yoy", "FY2025").value - 0.25) < 1e-9
    # fcf FY2025 = 200, FY2024 = 150 -> 0.3333
    assert abs(_by_name(ms, "fcf_growth_yoy", "FY2025").value - (200.0 / 150.0 - 1)) < 1e-9
    # revenue_cagr_3y at FY2025 over FY2022: (1200/600)^(1/3)-1
    assert abs(_by_name(ms, "revenue_cagr_3y", "FY2025").value - ((1200.0 / 600.0) ** (1 / 3) - 1)) < 1e-9
    # qoq at Q2 FY2025: 320/300 - 1
    assert abs(_by_name(ms, "revenue_growth_qoq", "Q2 FY2025").value - (320.0 / 300.0 - 1)) < 1e-9


def test_cagr_skips_nonpositive_base():
    f = _facts([("EarningsPerShareDiluted", "FY2025", 5.0), ("EarningsPerShareDiluted", "FY2022", -1.0)])
    assert _by_name(compute_metrics(f, None), "eps_cagr_3y", "FY2025") is None


def test_per_share_and_quality():
    f = _facts([
        ("OperatingCashFlow", "FY2025", 300.0),
        ("CapitalExpenditures", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2024", 80.0),
        ("StockholdersEquity", "FY2025", 1000.0),
        ("NetIncomeLoss", "FY2025", 250.0),
        ("IncomeTaxExpenseBenefit", "FY2025", 50.0),
        ("Assets", "FY2025", 2500.0),
        ("DividendsPaid", "FY2025", 50.0),
    ])
    ms = compute_metrics(f, None)
    assert abs(_by_name(ms, "fcf_per_share", "FY2025").value - 2.0) < 1e-9      # 200/100
    assert abs(_by_name(ms, "book_value_per_share", "FY2025").value - 10.0) < 1e-9
    # share_count_change_yoy = 100/80 - 1 = 0.25
    assert abs(_by_name(ms, "share_count_change_yoy", "FY2025").value - 0.25) < 1e-9
    # dividend_coverage = fcf 200 / dividends 50 = 4.0
    assert abs(_by_name(ms, "dividend_coverage", "FY2025").value - 4.0) < 1e-9
    # accruals_ratio = (250 - 300) / 2500 = -0.02
    assert abs(_by_name(ms, "accruals_ratio", "FY2025").value - (-0.02)) < 1e-9


def test_effective_tax_rate_not_duplicated():
    f = _facts([("NetIncomeLoss", "FY2025", 200.0), ("IncomeTaxExpenseBenefit", "FY2025", 50.0)])
    etrs = [m for m in compute_metrics(f, None) if m.name == "effective_tax_rate" and m.fiscal_period == "FY2025"]
    assert len(etrs) == 1


def _quote(market_cap=10_000.0):
    return Quote(price=100.0, market_cap=market_cap, currency="USD", provenance=Provenance(source="yfinance"))


def test_ttm_and_valuation():
    rows = []
    # 4 single quarters of revenue/net income/eps -> TTM
    for i, q in enumerate(["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"]):
        rows += [("Revenues", q, 250.0), ("NetIncomeLoss", q, 50.0), ("EarningsPerShareDiluted", q, 1.0)]
    rows += [
        ("StockholdersEquity", "FY2025", 5000.0),
        ("OperatingCashFlow", "FY2025", 1200.0),
        ("CapitalExpenditures", "FY2025", 200.0),
        ("DepreciationAndAmortization", "FY2025", 100.0),
        ("OperatingIncomeLoss", "FY2025", 900.0),
        ("LongTermDebt", "FY2025", 1000.0),
        ("CashAndCashEquivalents", "FY2025", 400.0),
        ("DividendsPaid", "FY2025", 100.0),
        ("StockRepurchased", "FY2025", 300.0),
        ("Revenues", "FY2025", 1000.0),
        ("NetIncomeLoss", "FY2025", 200.0),
    ]
    ms = compute_metrics(_facts(rows), _quote(market_cap=10_000.0))
    # TTM: revenue 1000, net income 200, eps 4
    assert abs(_by_name(ms, "revenue_ttm", "TTM").value - 1000.0) < 1e-9
    assert abs(_by_name(ms, "net_income_ttm", "TTM").value - 200.0) < 1e-9
    assert abs(_by_name(ms, "eps_ttm", "TTM").value - 4.0) < 1e-9
    # pe = 10000 / 200 = 50 ; ps = 10000/1000 = 10 ; pb = 10000/5000 = 2
    assert abs(_by_name(ms, "pe_ratio", "TTM").value - 50.0) < 1e-9
    assert abs(_by_name(ms, "ps_ratio", "TTM").value - 10.0) < 1e-9
    assert abs(_by_name(ms, "pb_ratio", "FY2025").value - 2.0) < 1e-9
    # net_debt = 600 ; EV = 10600 ; EBITDA = 1000 ; ev_ebitda = 10.6
    assert abs(_by_name(ms, "ev_ebitda", "FY2025").value - 10.6) < 1e-9
    # dividend_yield = 100/10000 = 0.01 ; buyback_yield = 300/10000 = 0.03 ; total = 0.04
    assert abs(_by_name(ms, "dividend_yield", "FY2025").value - 0.01) < 1e-9
    assert abs(_by_name(ms, "total_shareholder_yield", "FY2025").value - 0.04) < 1e-9


def test_valuation_skipped_without_quote():
    f = _facts([("NetIncomeLoss", "FY2025", 200.0)])
    assert _by_name(compute_metrics(f, None), "pe_ratio", "TTM") is None


def test_flow_over_stock_ratios_are_annual_only():
    # A quarterly FLOW (3-month revenue/COGS/income) over a point-in-time STOCK
    # (assets/equity/inventory) is a period mismatch (~1/4 scale) -> emit annual only.
    rows = [
        ("Revenues", "FY2025", 1000.0), ("Revenues", "Q2 FY2025", 250.0),
        ("Assets", "FY2025", 2000.0), ("Assets", "Q2 FY2025", 2000.0),
        ("NetIncomeLoss", "FY2025", 200.0), ("NetIncomeLoss", "Q2 FY2025", 50.0),
        ("StockholdersEquity", "FY2025", 1000.0), ("StockholdersEquity", "Q2 FY2025", 1000.0),
        ("CostOfRevenue", "FY2025", 600.0), ("CostOfRevenue", "Q2 FY2025", 150.0),
        ("Inventory", "FY2025", 300.0), ("Inventory", "Q2 FY2025", 300.0),
        ("OperatingIncomeLoss", "FY2025", 250.0), ("OperatingIncomeLoss", "Q2 FY2025", 60.0),
        ("LiabilitiesCurrent", "FY2025", 500.0), ("LiabilitiesCurrent", "Q2 FY2025", 500.0),
    ]
    ms = compute_metrics(_facts(rows), None)
    for name in ("roe", "roa", "roce", "asset_turnover", "inventory_turnover"):
        assert _by_name(ms, name, "FY2025") is not None, f"{name} annual missing"
        assert _by_name(ms, name, "Q2 FY2025") is None, f"{name} should be annual-only"
    # a flow/flow ratio (margin) is still emitted quarterly
    assert _by_name(ms, "net_margin", "Q2 FY2025") is not None


def test_per_share_growth_skipped_on_split_like_share_change():
    rows = [
        ("EarningsPerShareDiluted", "FY2025", 5.0), ("EarningsPerShareDiluted", "FY2024", 30.0),
        ("WeightedAverageSharesDiluted", "FY2025", 4800.0), ("WeightedAverageSharesDiluted", "FY2024", 480.0),  # 10x -> split
    ]
    ms = compute_metrics(_facts(rows), None)
    assert _by_name(ms, "eps_growth_yoy", "FY2025") is None        # split-contaminated -> skipped
    assert _by_name(ms, "share_count_change_yoy", "FY2025") is None


def test_per_share_growth_emitted_on_normal_share_change():
    rows = [
        ("EarningsPerShareDiluted", "FY2025", 6.0), ("EarningsPerShareDiluted", "FY2024", 5.0),
        ("WeightedAverageSharesDiluted", "FY2025", 1020.0), ("WeightedAverageSharesDiluted", "FY2024", 1000.0),  # +2%
    ]
    ms = compute_metrics(_facts(rows), None)
    assert abs(_by_name(ms, "eps_growth_yoy", "FY2025").value - 0.2) < 1e-9
    assert abs(_by_name(ms, "share_count_change_yoy", "FY2025").value - 0.02) < 1e-9


def test_stale_periods_dropped_outside_recency_window():
    rows = [
        ("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", 200.0),
        ("Revenues", "FY2018", 100.0), ("NetIncomeLoss", "FY2018", 20.0),
    ]
    ms = compute_metrics(_facts(rows), None)
    assert _by_name(ms, "net_margin", "FY2025") is not None
    assert _by_name(ms, "net_margin", "FY2018") is None   # >4 years older than latest -> dropped


def test_ttm_bridges_over_missing_q4():
    # Realistic mid-year: 3 current-year quarters + prior full FY + prior-year quarters.
    # TTM(ending Q3 FY2026) = FY2025 + (Q1+Q2+Q3 FY2026) - (Q1+Q2+Q3 FY2025), because
    # there is never a standalone Q4 10-Q to sum.
    rows = [
        ("Revenues", "FY2025", 280.0),
        ("Revenues", "Q1 FY2026", 78.0), ("Revenues", "Q2 FY2026", 81.0), ("Revenues", "Q3 FY2026", 83.0),
        ("Revenues", "Q1 FY2025", 62.0), ("Revenues", "Q2 FY2025", 64.0), ("Revenues", "Q3 FY2025", 66.0),
    ]
    ttm = _by_name(compute_metrics(_facts(rows), None), "revenue_ttm", "TTM")
    # 280 + (78+81+83) - (62+64+66) = 280 + 242 - 192 = 330
    assert ttm is not None and abs(ttm.value - 330.0) < 1e-9


def test_ttm_uses_annual_when_year_closed():
    # Full FY2025 annual plus its Q1-Q3 (right after the 10-K) -> TTM = the annual.
    rows = [
        ("Revenues", "FY2025", 280.0),
        ("Revenues", "Q1 FY2025", 62.0), ("Revenues", "Q2 FY2025", 64.0), ("Revenues", "Q3 FY2025", 66.0),
    ]
    ttm = _by_name(compute_metrics(_facts(rows), None), "revenue_ttm", "TTM")
    assert ttm is not None and abs(ttm.value - 280.0) < 1e-9


def test_valuation_falls_back_to_fy_when_quarters_incomplete():
    # Partial FY2026 (2 quarters) with no prior-year quarters to align the window ->
    # bridge can't be formed -> no TTM -> valuation uses latest FY, FY-labeled.
    f = _facts([
        ("NetIncomeLoss", "FY2025", 200.0),
        ("Revenues", "FY2025", 1000.0),
        ("StockholdersEquity", "FY2025", 5000.0),
        ("NetIncomeLoss", "Q2 FY2026", 50.0),
        ("NetIncomeLoss", "Q1 FY2026", 40.0),
    ])
    ms = compute_metrics(f, _quote(market_cap=10_000.0))
    assert _by_name(ms, "net_income_ttm", "TTM") is None        # bridge pieces missing -> no TTM
    pe = _by_name(ms, "pe_ratio", "FY2025")                     # falls back to latest FY, FY-labeled
    assert pe is not None and abs(pe.value - 50.0) < 1e-9       # 10000 / 200
    assert _by_name(ms, "pe_ratio", "TTM") is None


def test_no_fabricated_q2_q3_single_quarter_fcf():
    # Annual + Q1 cash flow exist (Q1 YTD == the quarter, retained upstream); Q2/Q3 have
    # no OCF/CapEx, so single-quarter FCF must NOT be fabricated for them.
    f = _facts([
        ("OperatingCashFlow", "FY2025", 1000.0), ("CapitalExpenditures", "FY2025", 300.0),
        ("OperatingCashFlow", "Q1 FY2025", 200.0), ("CapitalExpenditures", "Q1 FY2025", 60.0),
        ("Revenues", "Q2 FY2025", 500.0), ("Revenues", "Q3 FY2025", 600.0),
    ])
    ms = compute_metrics(f, None)
    assert _by_name(ms, "fcf", "FY2025") is not None            # annual FCF
    assert _by_name(ms, "fcf", "Q1 FY2025") is not None         # Q1 single-quarter FCF
    assert _by_name(ms, "fcf", "Q2 FY2025") is None             # not fabricated
    assert _by_name(ms, "fcf", "Q3 FY2025") is None             # not fabricated

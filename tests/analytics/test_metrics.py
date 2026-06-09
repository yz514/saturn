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

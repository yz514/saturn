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

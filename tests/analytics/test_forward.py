from saturn.analytics.forward import (
    _dcf,
    _solve_implied_growth,
    _solve_implied_return,
)


def test_dcf_matches_hand_computation():
    # fcf0=100, g=0, r=10%, n=2, terminal g_t=2.5%
    # PV = 100/1.1 + 100/1.21 + (100*1.025/0.075)/1.21
    #    = 90.909 + 82.645 + 1129.477 = 1303.03
    assert abs(_dcf(100.0, 0.0, 0.10, n=2, g_t=0.025) - 1303.03) < 0.1


def test_dcf_monotonic_in_discount_rate():
    # higher discount rate -> lower present value
    assert _dcf(100.0, 0.10, 0.08) > _dcf(100.0, 0.10, 0.10) > _dcf(100.0, 0.10, 0.12)


def test_solve_implied_growth_round_trips():
    target = _dcf(100.0, 0.12, 0.10)
    g, converged = _solve_implied_growth(100.0, target, 0.10)
    assert converged and abs(g - 0.12) < 1e-4


def test_solve_implied_growth_clamps_when_out_of_range():
    # an enormous target implies more growth than the +60% ceiling
    huge = _dcf(100.0, 0.60, 0.10) * 100
    g, converged = _solve_implied_growth(100.0, huge, 0.10)
    assert not converged and abs(g - 0.60) < 1e-9   # clamped to upper bound


def test_solve_implied_return_round_trips():
    target = _dcf(100.0, 0.05, 0.09)
    r = _solve_implied_return(100.0, 0.05, target)
    assert r is not None and abs(r - 0.09) < 1e-4


from saturn.analytics.forward import compute_forward
from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

_PROV = Provenance(source="SEC EDGAR")


def _ff(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=_PROV)
        for (c, p, v) in rows
    ])


def _quote(mc=1_000_000.0):
    return Quote(price=100.0, market_cap=mc, currency="USD", provenance=Provenance(source="yfinance"))


def _names(metrics):
    return {m.name for m in metrics}


def _positive_fcf_rows():
    # growing positive FCF (OCF - CapEx) across FY2022..FY2025 + shares
    rows = []
    for i, fy in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        rows += [("OperatingCashFlow", fy, 500.0 + 100 * i), ("CapitalExpenditures", fy, 50.0)]
        rows.append(("WeightedAverageSharesDiluted", fy, 100.0))
    return rows


def test_compute_forward_emits_all_seven_with_model_provenance():
    ms = compute_forward(_ff(_positive_fcf_rows()), _quote())
    assert _names(ms) == {
        "implied_fcf_growth", "expectations_gap", "implied_return",
        "reverse_dcf_fair_value_per_share", "reverse_dcf_value_low_per_share",
        "reverse_dcf_value_high_per_share", "margin_of_safety",
    }
    for m in ms:
        assert m.provenance.source == "Saturn (model)"
        assert m.fiscal_period == "model"
        assert any(i.concept == "market_cap" for i in m.inputs) or any("Cash" in i.concept or "Capital" in i.concept for i in m.inputs)


def test_compute_forward_fair_value_low_lt_high():
    ms = {m.name: m.value for m in compute_forward(_ff(_positive_fcf_rows()), _quote())}
    assert ms["reverse_dcf_value_low_per_share"] < ms["reverse_dcf_fair_value_per_share"] < ms["reverse_dcf_value_high_per_share"]


def test_compute_forward_expectations_gap_is_implied_minus_cagr():
    ms = {m.name: m.value for m in compute_forward(_ff(_positive_fcf_rows()), _quote())}
    # trailing 3y FCF CAGR from (500-50)=450 -> (800-50)=750: (750/450)**(1/3)-1
    cagr = (750.0 / 450.0) ** (1 / 3) - 1
    assert abs(ms["expectations_gap"] - (ms["implied_fcf_growth"] - cagr)) < 1e-9


def test_compute_forward_skips_on_nonpositive_fcf():
    rows = [("OperatingCashFlow", "FY2025", 100.0), ("CapitalExpenditures", "FY2025", 200.0)]  # FCF < 0
    assert compute_forward(_ff(rows), _quote()) == []


def test_compute_forward_skips_without_quote():
    assert compute_forward(_ff(_positive_fcf_rows()), None) == []


def test_implied_growth_records_clamp_flag_when_out_of_range():
    # A tiny FCF against a huge market cap implies growth beyond the +60% ceiling.
    rows = [("OperatingCashFlow", "FY2025", 100.0), ("CapitalExpenditures", "FY2025", 1.0)]
    ms = compute_forward(_ff(rows), _quote(mc=10_000_000_000.0))
    g = next(m for m in ms if m.name == "implied_fcf_growth")
    assert abs(g.value - 0.60) < 1e-9   # clamped to the upper search bound
    assert any(i.concept == "implied_growth_clamped_to_bound" for i in g.inputs)


def test_implied_growth_no_clamp_flag_on_normal_solve():
    rows = []
    for i, fy in enumerate(["FY2022", "FY2023", "FY2024", "FY2025"]):
        rows += [("OperatingCashFlow", fy, 500.0 + 100 * i), ("CapitalExpenditures", fy, 50.0),
                 ("WeightedAverageSharesDiluted", fy, 100.0)]
    ms = compute_forward(_ff(rows), _quote(mc=20_000.0))
    g = next(m for m in ms if m.name == "implied_fcf_growth")
    assert not any(i.concept == "implied_growth_clamped_to_bound" for i in g.inputs)

from saturn.analytics.driver import compute_driver_model
from saturn.models import ConsensusSnapshot, FinancialFact, Fundamentals, Provenance, Quote

PROV = Provenance(source="SEC EDGAR")


def _facts(rows):
    return Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=PROV)
        for (c, p, v) in rows
    ])


def _base_rows():
    # FY2025 revenue 1000 (TTM falls back to FY), FY2022 800 -> 3y CAGR = (1000/800)^(1/3)-1 ~ 0.0772
    return [
        ("Revenues", "FY2025", 1000.0), ("Revenues", "FY2022", 800.0),
        ("NetIncomeLoss", "FY2025", 100.0),
        ("WeightedAverageSharesDiluted", "FY2025", 50.0),
    ]


def _quote():
    return Quote(price=100.0, market_cap=5000.0, currency="USD", provenance=Provenance(source="yfinance"))


def test_driver_bridge_math_no_consensus():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None)
    assert dm is not None
    assert abs(dm.trailing_net_margin - 0.10) < 1e-9
    assert abs(dm.trailing_revenue_growth - ((1000 / 800) ** (1 / 3) - 1)) < 1e-9
    exp = 1000 * (1 + dm.trailing_revenue_growth) * 0.10 / 50
    assert abs(dm.saturn_eps - exp) < 1e-9
    assert dm.consensus_eps is None and dm.eps_gap is None
    assert dm.low_confidence is False


def test_driver_consensus_decomposition_two_lenses():
    cons = ConsensusSnapshot(forward_eps=2.5, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.consensus_eps == 2.5
    assert abs(dm.eps_gap - (dm.saturn_eps - 2.5)) < 1e-9
    assert abs(dm.consensus_implied_growth - 0.25) < 1e-9   # (2.5*50/0.1)/1000 - 1
    exp_m = 2.5 * 50 / (1000 * (1 + dm.trailing_revenue_growth))
    assert abs(dm.consensus_implied_margin - exp_m) < 1e-9


def test_driver_soft_fails_without_shares():
    rows = [("Revenues", "FY2025", 1000.0), ("NetIncomeLoss", "FY2025", 100.0)]  # no shares
    assert compute_driver_model(_facts(rows), _quote(), None) is None


def test_driver_low_confidence_on_negative_margin():
    rows = [("Revenues", "FY2025", 1000.0), ("Revenues", "FY2022", 800.0),
            ("NetIncomeLoss", "FY2025", -50.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None)
    assert dm is not None and dm.low_confidence is True
    assert any("margin" in c for c in dm.caveats)


def test_driver_low_confidence_without_growth_history():
    rows = [("Revenues", "FY2025", 1000.0),  # no FY2022 -> no 3y CAGR
            ("NetIncomeLoss", "FY2025", 100.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None)
    assert dm is not None and dm.trailing_revenue_growth == 0.0 and dm.low_confidence is True


def test_driver_low_confidence_extreme_implied_growth():
    # margin=0.1, shares=50, rev=1000 -> implied_g = (eps*50/0.1)/1000 - 1
    # forward_eps=4.0 -> implied_g = (4.0*50/0.1)/1000 - 1 = 2000/1000 - 1 = 1.0 (>0.60) -> low confidence
    cons = ConsensusSnapshot(forward_eps=4.0, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm is not None
    assert abs(dm.consensus_implied_growth - 1.0) < 1e-9
    assert dm.low_confidence is True
    assert any("extreme" in c for c in dm.caveats)


def test_driver_growth_override_uses_guidance_growth():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None, growth_override=0.15)
    assert abs(dm.trailing_revenue_growth - 0.15) < 1e-9
    assert dm.growth_source == "guidance"
    exp = 1000 * 1.15 * 0.10 / 50
    assert abs(dm.saturn_eps - exp) < 1e-9


def test_driver_growth_override_suppresses_no_history_caveat():
    rows = [("Revenues", "FY2025", 1000.0),  # no FY2022 -> no trailing CAGR
            ("NetIncomeLoss", "FY2025", 100.0), ("WeightedAverageSharesDiluted", "FY2025", 50.0)]
    dm = compute_driver_model(_facts(rows), _quote(), None, growth_override=0.12)
    assert dm.trailing_revenue_growth == 0.12 and dm.growth_source == "guidance"
    assert not any("no 3-year revenue history" in c for c in dm.caveats)


def test_driver_without_override_is_trend():
    dm = compute_driver_model(_facts(_base_rows()), _quote(), None)
    assert dm.growth_source == "trend"


def test_driver_waterfall_identity_and_values():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps=2.5, forward_revenue=1100.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    # consensus growth = 1100/1000 - 1 = 0.10; consensus margin = 2.5*50/1100
    assert abs(dm.consensus_growth - 0.10) < 1e-9
    assert abs(dm.consensus_margin - (2.5 * 50 / 1100)) < 1e-9
    # 2-factor identity: growth effect + margin effect == consensus_eps - saturn_eps
    assert abs((dm.gap_from_growth + dm.gap_from_margin) - (2.5 - dm.saturn_eps)) < 1e-6
    assert dm.consensus_revenue == 1100.0


def test_driver_no_waterfall_without_forward_revenue():
    from saturn.models import ConsensusSnapshot, Provenance
    cons = ConsensusSnapshot(forward_eps=2.5, provenance=Provenance(source="yfinance (estimate)"))
    dm = compute_driver_model(_facts(_base_rows()), _quote(), cons)
    assert dm.consensus_revenue is None and dm.gap_from_growth is None and dm.gap_from_margin is None
    assert dm.consensus_implied_growth is not None   # two-lens still present

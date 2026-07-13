from saturn.models import DriverModel, CompanyDossier, Provenance
from datetime import date


def test_driver_model_defaults():
    dm = DriverModel(saturn_eps=2.15, trailing_revenue_growth=0.077, trailing_net_margin=0.10,
                     shares=50.0, provenance=Provenance(source="Saturn (model)"))
    assert dm.horizon == "NTM"
    assert dm.consensus_eps is None and dm.eps_gap is None
    assert dm.low_confidence is False and dm.caveats == []


def test_dossier_has_driver_model_field():
    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 7, 12))
    assert d.driver_model is None


def test_guidance_model():
    from saturn.models import Guidance, Provenance
    g = Guidance(period="FY", value=70e9, implied_growth=0.15,
                 quote="We expect FY revenue of ~$70B.", provenance=Provenance(source="SEC EDGAR (guidance)"))
    assert g.metric == "revenue" and g.period == "FY" and abs(g.implied_growth - 0.15) < 1e-9


def test_driver_model_growth_source_defaults_to_trend():
    from saturn.models import DriverModel, Provenance
    dm = DriverModel(saturn_eps=2.0, trailing_revenue_growth=0.1, trailing_net_margin=0.1, shares=50.0,
                     provenance=Provenance(source="Saturn (model)"))
    assert dm.growth_source == "trend" and dm.growth_citation == ""


def test_consensus_snapshot_has_forward_revenue():
    from saturn.models import ConsensusSnapshot, Provenance
    c = ConsensusSnapshot(provenance=Provenance(source="yfinance (estimate)"))
    assert c.forward_revenue is None


def test_driver_model_waterfall_fields_default_none():
    from saturn.models import DriverModel, Provenance
    dm = DriverModel(saturn_eps=2.0, trailing_revenue_growth=0.1, trailing_net_margin=0.1, shares=50.0,
                     provenance=Provenance(source="Saturn (model)"))
    assert dm.consensus_revenue is None and dm.consensus_growth is None and dm.consensus_margin is None
    assert dm.gap_from_growth is None and dm.gap_from_margin is None

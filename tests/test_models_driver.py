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

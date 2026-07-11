# tests/agents/test_synthesist.py
from datetime import date

from saturn.agents.synthesist import _resolve_anchor, _price_scenarios
from saturn.models import (
    CompanyDossier, ConsensusSnapshot, DerivedMetric, Provenance, Quote, ScenarioLeg,
)


def _dossier(**kw):
    base = dict(ticker="MU", name="Micron", generated_at=date(2026, 7, 10))
    base.update(kw)
    return CompanyDossier(**base)


def _leg(name="base", value=10.0, mult=15.0):
    return ScenarioLeg(name=name, period="FY2027", driver="d", metric="EPS",
                       metric_basis="adjusted", per_share_value=value, multiple=mult, multiple_basis="P/E")


def test_resolve_anchor_prefers_consensus():
    d = _dossier(consensus=ConsensusSnapshot(forward_pe=6.5, target_mean=180.0, rating="buy",
                 n_analysts=30, provenance=Provenance(source="yfinance (estimate)")))
    a = _resolve_anchor(d)
    assert a.source == "consensus" and a.metric == "Forward P/E" and a.value == 6.5 and a.unit == "x"


def test_resolve_anchor_falls_back_to_reverse_dcf():
    d = _dossier(derived_metrics=[DerivedMetric(name="implied_fcf_growth", value=0.14, format="percent",
                 fiscal_period="model", formula="f", provenance=Provenance(source="Saturn (model)"))])
    a = _resolve_anchor(d)
    assert a.source == "reverse_dcf_implied" and a.value == 0.14 and "14%" in a.text


def test_resolve_anchor_none_when_no_data():
    a = _resolve_anchor(_dossier())
    assert a.source == "none" and a.confidence == "low"


def test_price_scenarios_computes_price_and_return():
    legs = _price_scenarios([_leg(value=12.0, mult=20.0)], quote_price=200.0)
    assert legs[0].implied_price == 240.0
    assert abs(legs[0].implied_return_pct - 0.20) < 1e-9


def test_price_scenarios_no_quote_leaves_return_none():
    legs = _price_scenarios([_leg(value=10.0, mult=15.0)], quote_price=None)
    assert legs[0].implied_price == 150.0 and legs[0].implied_return_pct is None

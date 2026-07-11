# tests/agents/test_synthesist.py
from datetime import date

from saturn.agents.synthesist import _resolve_anchor, _price_scenarios, alpha_completeness
from saturn.models import (
    AlphaThesis, CompanyDossier, ConsensusSnapshot, DerivedMetric, ExpectationAnchor,
    Provenance, Quote, ScenarioLeg,
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
    assert a.unit == "fraction"


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


def test_price_scenarios_zero_quote_leaves_return_none():
    legs = _price_scenarios([_leg(value=10.0, mult=15.0)], quote_price=0.0)
    assert legs[0].implied_price == 150.0 and legs[0].implied_return_pct is None


def _complete_thesis(**kw):
    base = dict(
        anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
        stance="above_expectations", variant="Market underrates HBM margin durability.",
        rationale="r", confidence="medium", key_variable="HBM gross margin",
        falsifier="GM below 60% next 2 quarters", horizon="12-18 months",
        scenarios=[_leg("bull"), _leg("base"), _leg("bear")],
        provenance=Provenance(source="Saturn (synthesist)"))
    base.update(kw)
    return AlphaThesis(**base)


def test_completeness_complete_thesis_has_no_gaps():
    assert alpha_completeness(_complete_thesis()) == []


def test_completeness_flags_missing_pieces():
    gaps = alpha_completeness(_complete_thesis(falsifier="", scenarios=[_leg("bull"), _leg("base")]))
    assert any("falsifier" in g for g in gaps) and any("3 scenarios" in g for g in gaps)


def test_completeness_flags_none_anchor():
    t = _complete_thesis(anchor=ExpectationAnchor(source="none", text="x", confidence="low"))
    assert any("anchor" in g for g in alpha_completeness(t))


def test_completeness_flags_variant_too_long():
    long_variant = " ".join(["word"] * 60)
    gaps = alpha_completeness(_complete_thesis(variant=long_variant))
    assert any("too long" in g for g in gaps)


def test_completeness_flags_scenario_missing_period():
    bad = _leg("bull")
    bad.period = ""     # blank period on one leg
    gaps = alpha_completeness(_complete_thesis(scenarios=[bad, _leg("base"), _leg("bear")]))
    assert any("missing period" in g and "bull" in g for g in gaps)

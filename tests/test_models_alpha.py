# tests/test_models_alpha.py
from saturn.models import AlphaThesis, ExpectationAnchor, ScenarioLeg, Provenance


def _leg(**kw):
    base = dict(name="base", period="FY2027", driver="d", metric="EPS", metric_basis="adjusted",
                per_share_value=10.0, multiple=15.0, multiple_basis="P/E")
    base.update(kw)
    return ScenarioLeg(**base)


def test_scenario_leg_computed_fields_default_none():
    leg = _leg()
    assert leg.implied_price is None and leg.implied_return_pct is None


def test_alpha_thesis_defaults_allow_partial():
    # anchor + provenance required; LLM-supplied fields default so a partial parse still validates
    t = AlphaThesis(anchor=ExpectationAnchor(source="none", text="x", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert t.stance == "unclear" and t.variant == "" and t.scenarios == [] and t.incompleteness == []


def test_scenario_leg_rejects_bad_literal():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _leg(metric="revenue")   # not in [EPS, FCF/share, sales/share]

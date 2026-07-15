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


def test_alpha_prose_fields_excludes_derived():
    from saturn.models import ALPHA_PROSE_FIELDS
    # derived/computed fields must never be LLM-rewritable
    assert "stance" not in ALPHA_PROSE_FIELDS and "scenarios" not in ALPHA_PROSE_FIELDS
    assert "stance_basis" not in ALPHA_PROSE_FIELDS and "anchor" not in ALPHA_PROSE_FIELDS
    # the prose fields are present
    for f in ("variant", "rationale", "key_variable", "falsifier", "horizon"):
        assert f in ALPHA_PROSE_FIELDS


def test_coherence_issue_and_default_empty():
    from saturn.models import CoherenceIssue, AlphaThesis, ExpectationAnchor, Provenance
    issue = CoherenceIssue(check="monotonicity", severity="high", detail="bull below base")
    assert issue.check == "monotonicity" and issue.severity == "high"
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert a.coherence_issues == []


def test_coherence_issue_accepts_bull_below_spot():
    from saturn.models import CoherenceIssue
    issue = CoherenceIssue(check="bull_below_spot", severity="medium", detail="bull below spot")
    assert issue.check == "bull_below_spot"

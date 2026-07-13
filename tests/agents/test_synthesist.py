# tests/agents/test_synthesist.py
from datetime import date

from saturn.agents.synthesist import _resolve_anchor, _price_scenarios, alpha_completeness, _coherence_score
from saturn.models import (
    AlphaThesis, CompanyDossier, ConsensusSnapshot, DerivedMetric, ExpectationAnchor,
    Provenance, Quote, ScenarioLeg, CoherenceIssue,
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
        stance="above_consensus", variant="Market underrates HBM margin durability.",
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


import json as _json
from saturn.agents.synthesist import synthesize


def _valid_alpha_json():
    return _json.dumps({
        "stance": "above_consensus", "variant": "Market underrates HBM margin durability.",
        "rationale": "SCAs lock demand.", "confidence": "medium", "key_variable": "HBM gross margin",
        "falsifier": "GM below 60% within 2 quarters", "horizon": "12-18 months",
        "scenarios": [
            {"name": "bull", "period": "FY2027", "driver": "HBM scarcity persists", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 13.0, "multiple": 18.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "normalizing", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "oversupply", "metric": "EPS",
             "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 10.0, "multiple_basis": "P/E"}]})


class _AlphaLLM:
    def __init__(self, payload): self.payload = payload
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=alpha" in prompt
        return self.payload


def _dossier_with_quote():
    return _dossier(quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
                    consensus=ConsensusSnapshot(forward_pe=6.5, provenance=Provenance(source="yfinance (estimate)")))


def _analysis():
    from saturn.models import AnalysisSections
    return AnalysisSections(executive_summary="e", company_overview="o", business_segments="s",
        financial_snapshot="f", valuation_discussion="v", key_risks="r", open_questions="q")


def _debate():
    from saturn.models import DebateSections
    return DebateSections(bull_thesis="b", bear_thesis="be", final_view="fv")


def test_synthesize_builds_priced_thesis():
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_valid_alpha_json()))
    assert t is not None and t.stance == "above_consensus" and len(t.scenarios) == 3
    base = next(s for s in t.scenarios if s.name == "base")
    assert base.implied_price == 150.0 and t.anchor.source == "consensus"
    assert t.incompleteness == []            # complete
    assert t.provenance.source == "Saturn (synthesist)"


def test_synthesize_malformed_soft_fails_to_none():
    assert synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM("not json")) is None


def test_synthesize_drops_bad_leg_keeps_rest():
    bad = _json.loads(_valid_alpha_json())
    bad["scenarios"][0]["metric"] = "revenue"     # invalid literal -> that leg dropped
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_json.dumps(bad)))
    assert t is not None
    assert len(t.scenarios) == 2 and any("3 scenarios" in g for g in t.incompleteness)


def test_synthesize_sanitizes_bad_stance():
    d = _json.loads(_valid_alpha_json())
    d["stance"] = "STRONG BUY"                     # not a valid literal -> coerced to unclear
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_json.dumps(d)))
    assert t.stance == "unclear"


def test_synthesize_with_mock_client_renders():
    from saturn.llm.mock_client import MockLLMClient
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), MockLLMClient())
    assert t is not None and len(t.scenarios) == 3


def test_synthesize_reverse_dcf_anchor_end_to_end():
    # No consensus -> anchor should fall back to the reverse-DCF implied-growth model,
    # and still produce a fully priced thesis through the whole synthesize() path.
    d = _dossier(
        quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
        derived_metrics=[DerivedMetric(name="implied_fcf_growth", value=0.14, format="percent",
            fiscal_period="model", formula="f", provenance=Provenance(source="Saturn (model)"))])
    t = synthesize(_analysis(), _debate(), d, _AlphaLLM(_valid_alpha_json()))
    assert t is not None
    assert t.anchor.source == "reverse_dcf_implied" and t.anchor.unit == "fraction"
    assert len(t.scenarios) == 3
    base = next(s for s in t.scenarios if s.name == "base")
    assert base.implied_price == 150.0    # 10 x 15 from the shared valid-alpha payload


def test_derive_stance_matrix():
    from saturn.agents.synthesist import _derive_stance
    assert _derive_stance(0.60, 0.45) == "above_consensus"     # base well above target
    assert _derive_stance(0.11, 0.45) == "below_consensus"     # base well below target (MSFT)
    assert _derive_stance(0.42, 0.45) == "in_line_consensus"   # within the 10pp band
    assert _derive_stance(0.11, None) is None                  # no target -> keep LLM stance
    assert _derive_stance(None, 0.45) is None                  # no base return


def test_synthesize_overrides_stance_from_consensus_target():
    from saturn.models import ConsensusSnapshot
    # base leg 10x15=150 vs quote 100 -> +50%; consensus target +80% -> +50% <= +70% -> below_consensus,
    # overriding the LLM's declared "above_consensus".
    d = _dossier(quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
                 consensus=ConsensusSnapshot(target_mean=180.0, target_upside_pct=0.80,
                                             provenance=Provenance(source="yfinance (estimate)")))
    t = synthesize(_analysis(), _debate(), d, _AlphaLLM(_valid_alpha_json()))
    assert t.stance == "below_consensus"
    assert "base +50% vs consensus target +80%" in t.stance_basis


def test_synthesize_keeps_llm_stance_without_consensus_target():
    # consensus present but no target_upside_pct -> derive returns None -> keep LLM's stance.
    t = synthesize(_analysis(), _debate(), _dossier_with_quote(), _AlphaLLM(_valid_alpha_json()))
    assert t.stance == "above_consensus"                       # from the (updated) payload
    assert "no consensus target" in t.stance_basis


def test_synthesize_in_line_consensus_stance():
    from saturn.models import ConsensusSnapshot
    # base leg 10x15=150 vs quote 100 -> +50%; target +45% -> within +/-10pp band -> in_line_consensus
    d = _dossier(quote=Quote(price=100.0, provenance=Provenance(source="yfinance")),
                 consensus=ConsensusSnapshot(target_mean=145.0, target_upside_pct=0.45,
                                             provenance=Provenance(source="yfinance (estimate)")))
    t = synthesize(_analysis(), _debate(), d, _AlphaLLM(_valid_alpha_json()))
    assert t.stance == "in_line_consensus"
    assert "base +50% vs consensus target +45%" in t.stance_basis


def test_synthesize_system_frames_rationale_and_forbids_verdict():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    s = SYNTHESIZE_SYSTEM.lower()
    # rationale must be framed on the base-case-vs-anchor axis...
    assert "base-case" in s and "rationale" in s
    # ...and the model must NOT assert its own overall verdict / re-declare the stance
    assert "do not assert" in s
    assert "the system derives" in s
    # the old verdict-assertion clause is gone
    assert "state whether the view is above / in line with / below the anchor and why" not in s


def test_apply_alpha_corrections_splices_prose_and_preserves_derived():
    from saturn.agents.synthesist import apply_alpha_corrections
    orig = _complete_thesis()
    updated = apply_alpha_corrections(orig, {"rationale": "new rationale",
                                             "stance": "unclear", "scenarios": []})
    assert updated.rationale == "new rationale"        # prose spliced
    assert updated.stance == orig.stance               # derived stance untouched
    assert updated.scenarios == orig.scenarios         # scenarios untouched
    assert updated.anchor == orig.anchor               # anchor untouched


def test_apply_alpha_corrections_recomputes_incompleteness():
    from saturn.agents.synthesist import apply_alpha_corrections
    # emptying the falsifier should make the completeness gate flag it
    updated = apply_alpha_corrections(_complete_thesis(), {"falsifier": ""})
    assert any("falsifier" in g for g in updated.incompleteness)


from saturn.agents.synthesist import scenario_coherence


def _priced_leg(name, price, ret, value=10.0, mult=15.0, basis="P/E"):
    return ScenarioLeg(name=name, period="FY2027", driver="d", metric="EPS",
                       metric_basis="adjusted", per_share_value=value, multiple=mult,
                       multiple_basis=basis, implied_price=price, implied_return_pct=ret)


def _coh_thesis(legs, rationale=""):
    return AlphaThesis(
        anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
        stance="below_consensus", rationale=rationale, confidence="low",
        scenarios=legs, provenance=Provenance(source="Saturn (synthesist)"))


def test_coherence_flags_non_monotonic_prices():
    # bull priced BELOW bear -> high monotonicity issue
    legs = [_priced_leg("bull", 100.0, -0.1), _priced_leg("base", 150.0, 0.0),
            _priced_leg("bear", 200.0, 0.2)]
    issues = scenario_coherence(_coh_thesis(legs), _dossier())
    assert [i.check for i in issues] == ["monotonicity"]
    assert issues[0].severity == "high"


def test_coherence_clean_monotonic_table_has_no_issue():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, 0.0),
            _priced_leg("bear", 100.0, -0.2)]
    assert scenario_coherence(_coh_thesis(legs), _dossier()) == []


def test_coherence_flags_prose_vs_computed():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="Our base case implies ~+2% vs the Street's +7%.")
    issues = scenario_coherence(t, _dossier())
    assert [i.check for i in issues] == ["prose_vs_computed"]
    assert issues[0].severity == "medium"


def test_coherence_prose_matching_computed_is_clean():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="Our base case implies -40% vs the Street's +7%.")
    assert scenario_coherence(t, _dossier()) == []


def test_coherence_unparseable_prose_no_false_positive():
    legs = [_priced_leg("bull", 200.0, 0.2), _priced_leg("base", 150.0, -0.42),
            _priced_leg("bear", 100.0, -0.6)]
    t = _coh_thesis(legs, rationale="The base case is cautious given execution risk.")
    assert scenario_coherence(t, _dossier()) == []


def test_coherence_flags_multiple_horizon():
    # consensus forward P/E 38x on forward EPS 6.0; a P/E leg at 38x applied to EPS 3.6 (< 0.8*6.0)
    cons = ConsensusSnapshot(forward_pe=38.0, forward_eps=6.0,
                             provenance=Provenance(source="yfinance (estimate)"))
    legs = [_priced_leg("bull", 200.0, 0.2, value=4.8, mult=42.0),
            _priced_leg("base", 136.8, -0.42, value=3.6, mult=38.0),
            _priced_leg("bear", 81.2, -0.66, value=2.9, mult=28.0)]
    issues = scenario_coherence(_coh_thesis(legs), _dossier(consensus=cons))
    assert [i.check for i in issues] == ["multiple_horizon"]


def test_coherence_multiple_horizon_skipped_without_consensus():
    legs = [_priced_leg("base", 136.8, -0.42, value=3.6, mult=38.0)]
    assert scenario_coherence(_coh_thesis(legs), _dossier()) == []


def test_coherence_score_weights():
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"),
                    coherence_issues=[CoherenceIssue(check="monotonicity", severity="high", detail="x"),
                                      CoherenceIssue(check="prose_vs_computed", severity="medium", detail="y")])
    assert _coherence_score(a) == 3


def test_coherence_score_zero_when_clean():
    a = AlphaThesis(anchor=ExpectationAnchor(source="none", text="", confidence="low"),
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert _coherence_score(a) == 0

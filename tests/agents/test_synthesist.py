# tests/agents/test_synthesist.py
from datetime import date

from saturn.agents.synthesist import (
    _resolve_anchor, _price_scenarios, alpha_completeness, _coherence_score, _prose_math_claims,
)
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
    # bull priced BELOW bear -> high monotonicity issue (bull has positive return to isolate check)
    legs = [_priced_leg("bull", 100.0, 0.0), _priced_leg("base", 150.0, 0.5),
            _priced_leg("bear", 200.0, 1.0)]
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


def _bull_thesis(bull_ret, stance, bull_price=100.0):
    # prices monotonic (100>=90>=80) so ONLY the bull_below_spot check can fire; rationale empty so
    # prose_vs_computed is skipped; _dossier() has no consensus so multiple_horizon is skipped.
    legs = [_priced_leg("bull", bull_price, bull_ret),
            _priced_leg("base", 90.0, -0.3), _priced_leg("bear", 80.0, -0.5)]
    return AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                       stance=stance, rationale="", confidence="low", scenarios=legs,
                       provenance=Provenance(source="Saturn (synthesist)"))


def test_bull_below_spot_high_for_nonbearish_stances():
    for stance in ("above_consensus", "in_line_consensus", "unclear"):
        issues = scenario_coherence(_bull_thesis(-0.19, stance), _dossier())
        assert [i.check for i in issues] == ["bull_below_spot"]
        assert issues[0].severity == "high"


def test_bull_below_spot_medium_for_below_consensus():
    issues = scenario_coherence(_bull_thesis(-0.19, "below_consensus"), _dossier())
    assert [i.check for i in issues] == ["bull_below_spot"]
    assert issues[0].severity == "medium"


def test_bull_at_or_above_spot_no_issue():
    assert scenario_coherence(_bull_thesis(0.05, "in_line_consensus"), _dossier()) == []
    assert scenario_coherence(_bull_thesis(0.0, "in_line_consensus"), _dossier()) == []


def test_bull_none_return_no_issue():
    legs = [_priced_leg("bull", 100.0, None), _priced_leg("base", 90.0, -0.3),
            _priced_leg("bear", 80.0, -0.5)]
    t = AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                    stance="in_line_consensus", scenarios=legs,
                    provenance=Provenance(source="Saturn (synthesist)"))
    assert scenario_coherence(t, _dossier()) == []


def test_bull_below_spot_orders_after_monotonicity():
    # bull priced BELOW base (non-monotonic) AND bull return < 0 -> two issues, stable order
    issues = scenario_coherence(_bull_thesis(-0.19, "unclear", bull_price=70.0), _dossier())
    assert [i.check for i in issues] == ["monotonicity", "bull_below_spot"]


def test_synthesize_system_has_horizon_rule():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    s = SYNTHESIZE_SYSTEM.lower()
    assert "same horizon" in s and "never apply a forward multiple" in s


class _CapLLM:
    def __init__(self): self.prompt = ""
    def complete(self, system, prompt, *, model=None, max_tokens=8192):
        self.prompt = prompt
        return ('{"stance":"unclear","variant":"v","rationale":"r","confidence":"low",'
                '"key_variable":"k","falsifier":"f","horizon":"12m","scenarios":[]}')


class _FakeSections:
    def model_dump(self): return {}


def test_resynthesize_corrective_includes_arithmetic_hint():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.agents.synthesist import resynthesize_coherent
    from saturn.models import CoherenceIssue
    d = _mock_dossier("MU")
    d.consensus.forward_pe = 38.0
    d.consensus.forward_eps = 6.18
    d.driver_model.saturn_eps = 3.24
    llm = _CapLLM()
    resynthesize_coherent(_FakeSections(), _FakeSections(), d, llm,
                          [CoherenceIssue(check="multiple_horizon", severity="medium", detail="x")],
                          model=None)
    assert "horizon error" in llm.prompt
    assert "38x" in llm.prompt and "$6.18" in llm.prompt and "$3.24" in llm.prompt


def test_resynthesize_corrective_hint_omitted_without_consensus():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.agents.synthesist import resynthesize_coherent
    from saturn.models import CoherenceIssue
    d = _mock_dossier("MU")
    d.consensus = None
    llm = _CapLLM()
    resynthesize_coherent(_FakeSections(), _FakeSections(), d, llm,
                          [CoherenceIssue(check="monotonicity", severity="high", detail="x")],
                          model=None)
    assert "horizon error" not in llm.prompt         # hint guarded off, no crash
    assert "coherence checks" in llm.prompt           # base corrective still present


from saturn.agents.synthesist import align_prose_base_return


def _align_thesis(rationale="", variant="", base_ret=-0.47):
    legs = [_priced_leg("base", 100.0, base_ret)]
    return AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                       stance="below_consensus", variant=variant, rationale=rationale, confidence="low",
                       scenarios=legs, provenance=Provenance(source="Saturn (synthesist)"))


def test_align_corrects_divergent_rationale():
    t = _align_thesis(rationale="Our base case implies ~+6% vs the Street's +14%.", base_ret=-0.47)
    align_prose_base_return(t)
    assert "-47%" in t.rationale and "+6%" not in t.rationale
    assert not any(i.check == "prose_vs_computed" for i in scenario_coherence(t, _dossier()))


def test_align_noop_within_tolerance():
    t = _align_thesis(rationale="Our base case implies -46% vs the Street's +14%.", base_ret=-0.47)  # 1pp (rounding)
    align_prose_base_return(t)
    assert "-46%" in t.rationale


def test_align_noop_no_cue():
    t = _align_thesis(rationale="The base case is cautious and execution-dependent.", base_ret=-0.47)
    align_prose_base_return(t)
    assert t.rationale == "The base case is cautious and execution-dependent."


def test_align_noop_no_base_leg():
    t = AlphaThesis(anchor=ExpectationAnchor(source="consensus", text="c", confidence="medium"),
                    stance="below_consensus", rationale="Our base case implies +6% vs Street.",
                    scenarios=[_priced_leg("bull", 100.0, 0.1)],
                    provenance=Provenance(source="Saturn (synthesist)"))
    align_prose_base_return(t)
    assert "+6%" in t.rationale


def test_align_corrects_variant():
    t = _align_thesis(variant="Base case implies +6% as consensus overreaches.", rationale="", base_ret=-0.47)
    align_prose_base_return(t)
    assert "-47%" in t.variant


def test_align_positive_computed():
    t = _align_thesis(rationale="Our base case implies -5% vs the Street.", base_ret=0.12)
    align_prose_base_return(t)
    assert "+12%" in t.rationale


def test_anchor_uses_ntm_pe_derived_from_the_same_ntm_eps():
    # AMZN-like: price 254.96 / NTM EPS 9.32 = 27.4x. The anchor and the driver bridge now speak the
    # SAME EPS, so "27.4x" and "$9.32" reconcile by construction.
    from saturn.models import Quote
    cons = ConsensusSnapshot(forward_eps=9.88, forward_pe=25.79, forward_eps_ntm=9.32, ntm_weight=0.46,
                             provenance=Provenance(source="yfinance (estimate)"))
    d = _dossier(consensus=cons, quote=Quote(price=254.96, provenance=Provenance(source="yfinance")))
    a = _resolve_anchor(d)
    assert a.metric == "NTM P/E" and abs(a.value - 27.4) < 0.1 and a.unit == "x"
    assert "NTM P/E 27.4x" in a.text and "$9.32" in a.text
    assert "46% current FY / 54% next FY" in a.text
    assert "FY+1 P/E 25.8x" in a.text and "$9.88" in a.text      # conventional reference retained


def test_anchor_falls_back_to_forward_pe_without_ntm_eps():
    cons = ConsensusSnapshot(forward_pe=6.5, forward_eps=1.2, target_mean=180.0, rating="buy",
                             n_analysts=30, provenance=Provenance(source="yfinance (estimate)"))
    a = _resolve_anchor(_dossier(consensus=cons))
    assert a.metric == "Forward P/E" and a.value == 6.5
    # the no-NTM path must be byte-identical to pre-slice behaviour: legacy wording, no FY+1 relabel
    assert "forward P/E 6.5x" in a.text
    assert "FY+1 P/E" not in a.text and "NTM P/E" not in a.text


def test_prose_tolerance_is_rounding_only():
    from saturn.agents.synthesist import _PROSE_RETURN_TOL
    assert _PROSE_RETURN_TOL == 0.02


def test_align_corrects_ten_point_divergence():
    # AMZN case: prose said +12% while the table computed +22% -> 10pp slipped the old 15pp tolerance
    t = _align_thesis(rationale="Base case implies ~+12% vs the Street's +23%.", base_ret=0.22)
    align_prose_base_return(t)
    assert "+22%" in t.rationale and "+12%" not in t.rationale


def test_build_thesis_wires_prose_alignment():
    # guards that align_prose_base_return is actually CALLED inside _build_thesis (isolation unit
    # tests would still pass if the call were removed). Divergent prose -> corrected + no prose issue.
    from saturn.agents.synthesist import _build_thesis, _resolve_anchor
    from saturn.models import Quote
    d = _dossier(quote=Quote(price=200.0, provenance=Provenance(source="yfinance")))
    data = {"stance": "below_consensus", "variant": "v",
            "rationale": "Our base case implies ~+6% vs the Street.", "confidence": "low",
            "key_variable": "k", "falsifier": "f", "horizon": "12m",
            "scenarios": [
                {"name": "bull", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 24.0, "multiple_basis": "P/E"},
                {"name": "base", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"},
                {"name": "bear", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 8.0, "multiple_basis": "P/E"}]}
    t = _build_thesis(data, _resolve_anchor(d), d)      # base priced 100 -> -50% return
    assert "+6%" not in t.rationale                      # corrected inside _build_thesis
    assert "-50%" in t.rationale
    assert not any(i.check == "prose_vs_computed" for i in t.coherence_issues)


def test_prose_math_claims_parses_pair_and_price():
    text = "base FY2027E: 20.5 EPS × 22.5 P/E, yielding an implied price near $358."
    claims = _prose_math_claims(text)
    assert len(claims) == 1
    a, b, price, start, end = claims[0]
    assert a == 20.5 and b == 22.5 and price == 358.0
    assert text[start:end] == "358"          # span covers the NUMBER only, not the "$"


def test_prose_math_claims_accepts_ascii_x_and_commas():
    claims = _prose_math_claims("20.5 EPS x 22.5 P/E gives $1,461.25 per share")
    assert len(claims) == 1 and claims[0][:3] == (20.5, 22.5, 1461.25)


def test_prose_math_claims_price_none_when_too_far():
    text = "20.5 EPS × 22.5 P/E" + " filler" * 40 + " $358"      # >120 chars away
    claims = _prose_math_claims(text)
    assert len(claims) == 1 and claims[0][2] is None


def test_prose_math_claims_empty_without_a_pair():
    assert _prose_math_claims("The base case is cautious; the stock trades at $395.63.") == []


def _msft_legs():
    # prices monotonic; bull above spot; the (value, multiple) pairs are the table's truth
    return [_priced_leg("bull", 528.00, 0.33, value=22.0, mult=24.0),
            _priced_leg("base", 461.25, 0.17, value=20.5, mult=22.5),
            _priced_leg("bear", 351.50, -0.11, value=18.5, mult=19.0)]


def test_prose_arithmetic_flags_false_math():
    # 20.5 x 22.5 = 461.25, but the prose claims $358 -> the LLM's own arithmetic is false
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $358.")
    assert [i.check for i in scenario_coherence(t, _dossier())] == ["prose_arithmetic"]


def test_prose_arithmetic_passes_when_correct():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $461.")
    assert scenario_coherence(t, _dossier()) == []      # 461 vs 461.25 is rounding


def test_prose_arithmetic_skips_pair_without_a_price():
    t = _coh_thesis(_msft_legs(), rationale="our base rests on 20.5 EPS × 22.5 P/E across the cycle.")
    assert scenario_coherence(t, _dossier()) == []


def test_coherence_issue_accepts_the_two_new_checks():
    from saturn.models import CoherenceIssue
    for name in ("prose_arithmetic", "prose_scenario_not_in_table"):
        assert CoherenceIssue(check=name, severity="medium", detail="d").check == name


from saturn.agents.synthesist import align_prose_scenario_math


def test_align_prose_scenario_math_corrects_the_price():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E, an implied price near $358.")
    align_prose_scenario_math(t)
    assert "$461.25" in t.rationale and "$358" not in t.rationale
    assert not any(i.check == "prose_arithmetic" for i in scenario_coherence(t, _dossier()))


def test_align_prose_scenario_math_noop_when_correct():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E → $461.")
    align_prose_scenario_math(t)
    assert "$461." in t.rationale


def test_prose_scenario_not_in_table_flags_an_orphan_pair():
    # The real MSFT sin: 18.86 x 19 = 358.34 ~= $358, so the ARITHMETIC IS TRUE -- but 18.86x19 is not
    # a leg in the table. This is the check that catches a smuggled second base case.
    t = _coh_thesis(_msft_legs(), rationale="an alternative read: 18.86 EPS × 19x = $358.")
    checks = [i.check for i in scenario_coherence(t, _dossier())]
    assert "prose_scenario_not_in_table" in checks
    assert "prose_arithmetic" not in checks          # the math itself is correct


def test_prose_scenario_not_in_table_tolerance_is_one_percent():
    # REGRESSION GUARD: 18.86 sits 1.95% from the bear leg's 18.5. At a 2% tolerance it would be
    # matched to bear and escape. It must NOT be.
    t = _coh_thesis(_msft_legs(), rationale="an alternative read: 18.86 EPS × 19x = $358.")
    issue = next(i for i in scenario_coherence(t, _dossier()) if i.check == "prose_scenario_not_in_table")
    assert "18.86" in issue.detail


def test_prose_scenario_in_the_table_passes():
    t = _coh_thesis(_msft_legs(), rationale="base: 20.5 EPS × 22.5 P/E → $461.")
    assert scenario_coherence(t, _dossier()) == []


def test_prose_scenario_tolerates_rounding_of_a_real_leg():
    legs = [_priced_leg("bull", 528.00, 0.33, value=22.0, mult=24.0),
            _priced_leg("base", 461.25, 0.17, value=20.5, mult=22.5),
            _priced_leg("bear", 358.34, -0.10, value=18.86, mult=19.0)]
    # prose rounds 18.86 -> 18.9 (0.21% off) and 18.9*19 = 359.1 vs the stated $359 (0.03%)
    t = _coh_thesis(legs, rationale="bear: 18.9 EPS × 19x → $359.")
    assert scenario_coherence(t, _dossier()) == []


def test_align_prose_scenario_math_noop_without_a_pair():
    t = _coh_thesis(_msft_legs(), rationale="The base case is cautious.")
    align_prose_scenario_math(t)
    assert t.rationale == "The base case is cautious."


def test_align_prose_scenario_math_corrects_the_variant_field_too():
    t = _coh_thesis(_msft_legs(), rationale="")
    t.variant = "Base 20.5 EPS × 22.5 P/E implies $358."
    align_prose_scenario_math(t)
    assert "$461.25" in t.variant


def test_build_thesis_wires_scenario_math_alignment():
    # guards that align_prose_scenario_math is actually CALLED in _build_thesis — the unit tests above
    # would still pass if the call were deleted.
    from saturn.agents.synthesist import _build_thesis, _resolve_anchor
    from saturn.models import Quote
    d = _dossier(quote=Quote(price=400.0, provenance=Provenance(source="yfinance")))
    data = {"stance": "unclear", "variant": "v",
            "rationale": "base: 20.5 EPS × 22.5 P/E, an implied price near $358.",
            "confidence": "low", "key_variable": "k", "falsifier": "f", "horizon": "12m",
            "scenarios": [
                {"name": "bull", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 22.0, "multiple": 24.0, "multiple_basis": "P/E"},
                {"name": "base", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 20.5, "multiple": 22.5, "multiple_basis": "P/E"},
                {"name": "bear", "period": "FY", "driver": "d", "metric": "EPS",
                 "metric_basis": "adjusted", "per_share_value": 18.5, "multiple": 19.0, "multiple_basis": "P/E"}]}
    t = _build_thesis(data, _resolve_anchor(d), d)
    assert "$461.25" in t.rationale and "$358" not in t.rationale
    assert not any(i.check == "prose_arithmetic" for i in t.coherence_issues)

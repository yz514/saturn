from saturn.ingestion.dossier import _mock_dossier
from saturn.llm.mock_client import MockLLMClient
from saturn.workflows.equity_research import _extract_json, run


def test_run_with_mock_client_populates_report():
    report = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.mock is True
    assert report.model_used == "mock"
    assert report.analysis.executive_summary.startswith("[MOCK]")
    assert report.debate.bull_thesis.startswith("[MOCK]")
    assert report.sources == ["MOCK fixture data — not real market sources"]
    assert report.critic_review is not None


def test_run_real_mode_builds_provenance_sources():
    report = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="claude-x", mock=False)
    # In real mode, sources are collected from provenance fields
    assert len(report.sources) > 0
    # yfinance mock quote should appear as first source
    assert any("yfinance" in s for s in report.sources)


def test_extract_json_strips_code_fences():
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json(fenced) == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'


# ---- Critic-v2: self-repair loop ----

import json

_ANALYSIS_KEYS = ["executive_summary", "company_overview", "business_segments",
                  "financial_snapshot", "valuation_discussion", "key_risks", "open_questions"]


class _RepairLLM:
    """Stateful stub: analyze -> debate -> critic(1 high contradiction) -> revise -> critic(empty)."""
    def __init__(self, improve=True):
        self.improve = improve
        self.critic_calls = 0

    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "orig bear", "final_view": "f"})
        if "OUTPUT_SCHEMA=revise" in prompt:
            return json.dumps({"bear_thesis": "corrected bear"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            self.critic_calls += 1
            bad = [{"claim": "x", "section": "bear_thesis", "category": "contradiction",
                    "verdict": "contradicted", "evidence": "data shows y", "severity": "high"}]
            if self.critic_calls == 1:
                return json.dumps({"claims_checked": 3, "summary": "issue", "findings": bad})
            return json.dumps({"claims_checked": 3, "summary": "ok",
                               "findings": [] if self.improve else bad})
        return "{}"


def test_run_self_repair_corrects_and_flags_repaired():
    r = run(_mock_dossier("MU"), _RepairLLM(improve=True), model_used="m", mock=False)
    assert r.debate.bear_thesis == "corrected bear"
    assert r.critic_review is not None and r.critic_review.repaired is True


def test_run_self_repair_keeps_original_when_not_improved():
    r = run(_mock_dossier("MU"), _RepairLLM(improve=False), model_used="m", mock=False)
    assert r.debate.bear_thesis == "orig bear"                       # revision rejected
    assert r.critic_review is None or r.critic_review.repaired is False


def test_run_populates_alpha_thesis():
    r = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert r.alpha_thesis is not None and len(r.alpha_thesis.scenarios) == 3
    base = next(s for s in r.alpha_thesis.scenarios if s.name == "base")
    assert base.implied_price == 150.0            # 10 × 15 from the mock


_ALPHA_JSON = json.dumps({
    "stance": "below_consensus", "variant": "v", "rationale": "3-year CAGR near zero",
    "confidence": "medium", "key_variable": "k", "falsifier": "GM<60% in 2Q", "horizon": "12m",
    "scenarios": [
        {"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 13.0, "multiple": 18.0, "multiple_basis": "P/E"},
        {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
        {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS",
         "metric_basis": "adjusted", "per_share_value": 6.0, "multiple": 10.0, "multiple_basis": "P/E"}]})


class _AlphaRepairLLM:
    """analyze -> debate -> synth -> critic(1 high alpha finding) -> revise_alpha -> critic(clean)."""
    def __init__(self, improve=True):
        self.improve = improve
        self.critic_calls = 0
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            return _ALPHA_JSON
        if "OUTPUT_SCHEMA=revise_alpha" in prompt:
            return json.dumps({"rationale": "corrected rationale"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            self.critic_calls += 1
            finding = [{"claim": "3-year CAGR near zero", "section": "alpha_thesis",
                        "category": "unsupported_alpha_inference", "verdict": "unsupported",
                        "evidence": "data shows 3.9% 2-year", "severity": "high"}]
            if self.critic_calls == 1:
                return json.dumps({"claims_checked": 3, "summary": "x", "findings": finding})
            return json.dumps({"claims_checked": 3, "summary": "ok",
                               "findings": [] if self.improve else finding})
        return "{}"


def test_run_alpha_self_repair_corrects_and_flags():
    r = run(_mock_dossier("JNJ"), _AlphaRepairLLM(improve=True), model_used="m", mock=False)
    assert r.alpha_thesis is not None and r.alpha_thesis.rationale == "corrected rationale"
    assert r.critic_review is not None and r.critic_review.repaired is True


def test_run_alpha_self_repair_keeps_original_when_not_improved():
    r = run(_mock_dossier("JNJ"), _AlphaRepairLLM(improve=False), model_used="m", mock=False)
    assert r.alpha_thesis.rationale == "3-year CAGR near zero"      # revision rejected
    assert r.critic_review is None or r.critic_review.repaired is False


def test_company_context_includes_driver_model():
    from saturn.workflows.equity_research import _company_context
    from saturn.ingestion.dossier import _mock_dossier
    ctx = _company_context(_mock_dossier("NVDA"))
    assert "DRIVER MODEL" in ctx and "Saturn forward EPS" in ctx


def test_synthesize_system_references_driver_gap():
    from saturn.agents.synthesist import SYNTHESIZE_SYSTEM
    assert "driver" in SYNTHESIZE_SYSTEM.lower()


def _guidance_dossier():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.models import FilingSection, Provenance
    d = _mock_dossier("MSFT")
    d.filing_sections = list(d.filing_sections) + [FilingSection(
        name="Earnings release", excerpt="We expect full-year revenue of approximately $70 billion.",
        provenance=Provenance(source="SEC EDGAR"))]
    return d


class _GuidanceRunLLM:
    """Returns grounded revenue guidance; everything else is minimal valid JSON."""
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=guidance" in prompt:
            return '{"value": 70000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "o" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 0, "summary": "s", "findings": []})
        return "{}"


def test_run_uses_guidance_growth_when_grounded():
    r = run(_guidance_dossier(), _GuidanceRunLLM(), model_used="m", mock=False)
    assert r.company.driver_model is not None
    assert r.company.driver_model.growth_source == "guidance"
    assert "70 billion" in r.company.driver_model.growth_citation


def test_run_falls_back_to_trend_without_guidance():
    # MockLLMClient returns "{}" for the guidance prompt -> no guidance -> trend model retained
    r = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert r.company.driver_model is not None
    assert r.company.driver_model.growth_source == "trend"


class _QuarterGuidanceRunLLM:
    """Grounded QUARTERLY revenue guidance; everything else minimal valid JSON."""
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=guidance" in prompt:
            return '{"value": 18000000000, "period": "quarter", "quote": "We expect full-year revenue of approximately $70 billion."}'
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "o" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 0, "summary": "s", "findings": []})
        return "{}"


def test_run_quarter_guidance_adds_annualization_caveat():
    r = run(_guidance_dossier(), _QuarterGuidanceRunLLM(), model_used="m", mock=False)
    dm = r.company.driver_model
    assert dm is not None and dm.growth_source == "guidance"
    assert any("annualized from a quarterly" in c for c in dm.caveats)


def _incoherent_scenarios():
    # non-monotonic: bull price 100 < bear price 200
    return [{"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 20.0, "multiple_basis": "P/E"}]


def _coherent_scenarios():
    return [{"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 20.0, "multiple_basis": "P/E"},
            {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
            {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
             "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"}]


class _CoherenceRunLLM:
    """synth (incoherent) -> coherence gate -> re-synth (coherent iff improve) -> clean critic."""
    def __init__(self, improve=True):
        self.improve = improve
    def _alpha(self, scenarios):
        return json.dumps({"stance": "unclear", "variant": "v", "rationale": "r", "confidence": "low",
                           "key_variable": "k", "falsifier": "f", "horizon": "12m", "scenarios": scenarios})
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            if "coherence checks" in prompt:   # the corrective re-synthesis
                return self._alpha(_coherent_scenarios() if self.improve else _incoherent_scenarios())
            return self._alpha(_incoherent_scenarios())
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 1, "summary": "ok", "findings": []})
        return "{}"


def _coherence_dossier():
    d = _mock_dossier("MU")
    d.consensus = None   # isolate the monotonicity check (no consensus -> multiple_horizon skipped)
    return d


def test_run_coherence_gate_replaces_when_improved():
    r = run(_coherence_dossier(), _CoherenceRunLLM(improve=True), model_used="m", mock=False)
    assert r.alpha_thesis is not None and r.alpha_thesis.coherence_issues == []


def test_run_coherence_gate_keeps_original_when_not_improved():
    r = run(_coherence_dossier(), _CoherenceRunLLM(improve=False), model_used="m", mock=False)
    assert any(i.check == "monotonicity" for i in r.alpha_thesis.coherence_issues)


class _CoherenceReSynthFailsLLM(_CoherenceRunLLM):
    """The corrective re-synthesis returns unparseable JSON -> resynthesize_coherent soft-fails to
    None -> the gate must keep the original (incoherent) thesis, never break the report."""
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=alpha" in prompt and "coherence checks" in prompt:
            return "not valid json at all"
        return super().complete(system, prompt, model=model, max_tokens=max_tokens)


def test_run_coherence_gate_softfails_when_resynth_unparseable():
    r = run(_coherence_dossier(), _CoherenceReSynthFailsLLM(), model_used="m", mock=False)
    assert r.alpha_thesis is not None
    assert any(i.check == "monotonicity" for i in r.alpha_thesis.coherence_issues)


class _StalePromptCoherenceLLM:
    """prose_vs_computed survives the gate, then alpha-repair rewrites the rationale to remove the
    contradiction. The final thesis must have NO stale coherence issue (run() recomputes)."""
    def __init__(self):
        self.critic_calls = 0
    def _alpha(self, rationale):
        legs = [{"name": "bull", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
                 "per_share_value": 10.0, "multiple": 22.0, "multiple_basis": "P/E"},
                {"name": "base", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
                 "per_share_value": 10.0, "multiple": 15.0, "multiple_basis": "P/E"},
                {"name": "bear", "period": "FY2027", "driver": "d", "metric": "EPS", "metric_basis": "adjusted",
                 "per_share_value": 10.0, "multiple": 10.0, "multiple_basis": "P/E"}]
        return json.dumps({"stance": "unclear", "variant": "v", "rationale": rationale, "confidence": "low",
                           "key_variable": "k", "falsifier": "f", "horizon": "12m", "scenarios": legs})
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "orig" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            # initial AND corrective re-synth both claim +5% (issue survives the gate)
            return self._alpha("Our base case implies +5% vs the Street's +3%.")
        if "OUTPUT_SCHEMA=revise_alpha" in prompt:
            return json.dumps({"rationale": "The base case reflects a cautious, execution-dependent view."})
        if "OUTPUT_SCHEMA=critic" in prompt:
            self.critic_calls += 1
            finding = [{"claim": "base +5% contradicts the table", "section": "alpha_thesis",
                        "category": "unsupported_alpha_inference", "verdict": "unsupported",
                        "evidence": "computed base is -25%", "severity": "high"}]
            if self.critic_calls == 1:
                return json.dumps({"claims_checked": 1, "summary": "x", "findings": finding})
            return json.dumps({"claims_checked": 1, "summary": "ok", "findings": []})
        return "{}"


def _stale_coherence_dossier():
    d = _mock_dossier("MU")
    d.consensus = None       # isolate prose_vs_computed (no multiple_horizon)
    d.quote.price = 200.0    # base 10*15=150 -> computed base return -25%, vs prose +5% -> issue fires
    return d


def test_run_recomputes_coherence_after_alpha_repair():
    r = run(_stale_coherence_dossier(), _StalePromptCoherenceLLM(), model_used="m", mock=False)
    assert r.alpha_thesis is not None
    assert "cautious" in r.alpha_thesis.rationale                 # alpha-repair rewrote the prose
    assert r.alpha_thesis.coherence_issues == []                  # stale prose_vs_computed cleared


def _mp_legs(bull, base, bear):
    # each arg is (per_share_value, multiple); price = value*multiple, return computed vs quote
    def leg(name, vm):
        return {"name": name, "period": "FY2027", "driver": "d", "metric": "EPS",
                "metric_basis": "adjusted", "per_share_value": vm[0], "multiple": vm[1],
                "multiple_basis": "P/E"}
    return [leg("bull", bull), leg("base", base), leg("bear", bear)]


class _MultiPassLLM:
    """Returns `initial` on the first synthesize; on each corrective re-synthesis (prompt contains
    'coherence checks') returns the next table from `resynth_tables` (clamped to the last)."""
    def __init__(self, stance, initial, resynth_tables):
        self.stance = stance
        self.initial = initial
        self.resynth_tables = resynth_tables
        self.resynth = 0
    def _alpha(self, legs):
        return json.dumps({"stance": self.stance, "variant": "v", "rationale": "r",
                           "confidence": "low", "key_variable": "k", "falsifier": "f",
                           "horizon": "12m", "scenarios": legs})
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        if "OUTPUT_SCHEMA=analysis" in prompt:
            return json.dumps({k: "o" for k in _ANALYSIS_KEYS})
        if "OUTPUT_SCHEMA=debate" in prompt:
            return json.dumps({"bull_thesis": "b", "bear_thesis": "be", "final_view": "f"})
        if "OUTPUT_SCHEMA=alpha" in prompt:
            if "coherence checks" in prompt:
                t = self.resynth_tables[min(self.resynth, len(self.resynth_tables) - 1)]
                self.resynth += 1
                return self._alpha(t)
            return self._alpha(self.initial)
        if "OUTPUT_SCHEMA=critic" in prompt:
            return json.dumps({"claims_checked": 1, "summary": "ok", "findings": []})
        return "{}"


def _mp_dossier():
    d = _mock_dossier("MU")
    d.consensus = None       # no target -> stance stays LLM-declared; multiple_horizon skipped
    d.quote.price = 200.0    # controls implied returns: price 150 -> -25%, 190 -> -5%, 240 -> +20%
    return d


def test_run_multipass_two_passes_to_coherent():
    # stance 'unclear' -> bull_below_spot is HIGH(2). Scores: initial 4 -> pass1 2 -> pass2 0.
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # prices 150/180/220: non-monotonic(2) + bull -25%(2) = 4
    pass1 = _mp_legs((10, 19), (10, 17), (10, 15))     # prices 190/170/150: monotonic, bull -5%(2) = 2
    pass2 = _mp_legs((10, 24), (10, 21), (10, 18))     # prices 240/210/180: monotonic, bull +20% = 0
    llm = _MultiPassLLM("unclear", initial, [pass1, pass2])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert r.alpha_thesis.coherence_issues == []
    assert llm.resynth == 2


def test_run_multipass_stops_when_no_improvement():
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # score 4
    llm = _MultiPassLLM("unclear", initial, [initial])  # re-synth returns the same table -> no improvement
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 1                              # stopped after one non-improving pass
    assert r.alpha_thesis.coherence_issues != []


def test_run_multipass_already_coherent_no_resynth():
    coherent = _mp_legs((10, 24), (10, 21), (10, 18))   # monotonic, bull +20% -> score 0
    llm = _MultiPassLLM("unclear", coherent, [coherent])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 0
    assert r.alpha_thesis.coherence_issues == []


def test_run_multipass_caps_at_two_even_if_still_improving():
    # stance 'below_consensus' -> bull_below_spot is MEDIUM(1). Scores strictly improve 3->2->1 but a
    # 3rd pass (would be 0) is blocked by the cap; the loop stops at 2 with residual issues.
    initial = _mp_legs((10, 15), (10, 18), (10, 22))   # 150/180/220: non-monotonic(2) + bull -25% med(1) = 3
    pass1 = _mp_legs((10, 21), (10, 24), (10, 26))     # 210/240/260: non-monotonic(2), bull +5% = 2
    pass2 = _mp_legs((10, 19), (10, 17), (10, 15))     # 190/170/150: monotonic, bull -5% med(1) = 1
    pass3 = _mp_legs((10, 24), (10, 21), (10, 18))     # would be 0, but never reached
    llm = _MultiPassLLM("below_consensus", initial, [pass1, pass2, pass3])
    r = run(_mp_dossier(), llm, model_used="m", mock=False)
    assert llm.resynth == 2                             # capped
    assert r.alpha_thesis.coherence_issues != []        # residual issue remains

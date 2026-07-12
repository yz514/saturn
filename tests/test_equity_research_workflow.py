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

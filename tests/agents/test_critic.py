"""Tests for saturn/agents/critic.py — grounding helper and critique()."""
from datetime import date

from saturn.agents.critic import is_dollar_grounded, is_number_grounded
from saturn.models import CompanyDossier, FilingSection, DerivedMetric, Provenance


def _dossier():
    return CompanyDossier(
        ticker="MU", name="Micron",
        fundamentals=None,
        derived_metrics=[DerivedMetric(name="revenue_ttm", value=90_274_000_000.0, format="currency",
                        fiscal_period="TTM", formula="f", provenance=Provenance(source="Saturn (derived)"))],
        filing_sections=[FilingSection(name="Business Unit / Segment Results (earnings release)",
                        excerpt="adjusted free cash flow was $18.3 billion", provenance=Provenance(source="SEC EDGAR"))],
        generated_at=date(2026, 7, 10),
    )


def test_dollar_grounded_matches_metric():
    assert is_dollar_grounded("$90.3B", _dossier()) is True     # ~ revenue_ttm


def test_dollar_grounded_matches_source_text():
    assert is_dollar_grounded("$18.3B", _dossier()) is True     # in the press-release excerpt


def test_dollar_not_grounded():
    assert is_dollar_grounded("$999B", _dossier()) is False


# ---- Task 3: critique() tests ----

from saturn.agents.critic import critique
from saturn.models import AnalysisSections, DebateSections


def _analysis():
    return AnalysisSections(executive_summary="Fair value $16.34 is the key takeaway.",
        company_overview="o", business_segments="Cloud is the fastest-growing segment.",
        financial_snapshot="s", valuation_discussion="v", key_risks="r", open_questions="q")


def _debate():
    return DebateSections(bull_thesis="b", bear_thesis="Data shows margins of $999B somewhere.", final_view="f")


class _CriticLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=critic" in prompt
        return ('{"claims_checked": 5, "summary": "issues found", "findings": ['
                '{"claim": "$999B", "section": "bear_thesis", "category": "unsupported_number",'
                ' "verdict": "unsupported", "evidence": "not in data", "severity": "high"},'
                '{"claim": "$90.3B TTM revenue", "section": "financial_snapshot", "category": "unsupported_number",'
                ' "verdict": "unsupported", "evidence": "wrong", "severity": "low"}]}')


class _BrokenLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return "not json at all"


def test_critique_parses_and_applies_backstop():
    # dossier grounds $90.3B (revenue_ttm) but not $999B -> backstop drops the $90.3B false positive
    review = critique(_analysis(), _debate(), _dossier(), _CriticLLM())
    cats = [(f.claim, f.category) for f in review.findings]
    assert any("999" in c for c, _ in cats)                 # ungrounded kept
    assert not any("90.3" in c for c, _ in cats)            # grounded dropped by backstop
    assert review.provenance.source == "Saturn (critic)"


def test_critique_soft_fails_to_none():
    assert critique(_analysis(), _debate(), _dossier(), _BrokenLLM()) is None


class _MissingFieldLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        # a finding missing "severity" and "verdict" -> lenient model keeps it
        return ('{"claims_checked": 2, "summary": "s", "findings": ['
                '{"claim": "X", "section": "bull_thesis", "category": "contradiction", "evidence": "e"}]}')


class _RetryLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        self.calls += 1
        return "oops not json" if self.calls == 1 else '{"claims_checked": 1, "summary": "ok", "findings": []}'


def test_critique_keeps_finding_missing_optional_field():
    review = critique(_analysis(), _debate(), _dossier(), _MissingFieldLLM())
    assert review is not None and len(review.findings) == 1
    assert review.findings[0].severity == "medium"   # defaulted, not discarded


def test_critique_retries_once_then_succeeds():
    llm = _RetryLLM()
    review = critique(_analysis(), _debate(), _dossier(), llm)
    assert review is not None and llm.calls == 2


def test_dollar_grounded_ignores_year_prefix():
    d = _dossier()
    # must find the $90.3B token (~ revenue_ttm), not be fooled by "2025" in FY2025
    assert is_dollar_grounded("FY2025 revenue $90.3B", d) is True
    # a bare year is not a dollar figure -> not grounded
    assert is_dollar_grounded("during FY2025", d) is False


class _SupportedNoiseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return ('{"claims_checked": 2, "summary": "s", "findings": ['
                '{"claim": "FY2025 net income $8.5B", "section": "company_overview",'
                ' "category": "unsupported_number", "verdict": "unsupported",'
                ' "evidence": "NetIncomeLoss FY2025: 8539000000. Claim supported.", "severity": "low"},'
                '{"claim": "Cloud is fastest-growing", "section": "business_segments",'
                ' "category": "contradiction", "verdict": "contradicted",'
                ' "evidence": "Core DC +653% > Cloud +307%", "severity": "high"}]}')


def test_critique_drops_supported_noise():
    review = critique(_analysis(), _debate(), _dossier(), _SupportedNoiseLLM())
    cats = [f.category for f in review.findings]
    assert "contradiction" in cats            # real issue kept
    assert "unsupported_number" not in cats   # "Claim supported" noise dropped


def _pct_ratio_dossier():
    from datetime import date
    from saturn.models import CompanyDossier, DerivedMetric, Provenance
    prov = Provenance(source="Saturn (derived)")
    return CompanyDossier(
        ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        derived_metrics=[
            DerivedMetric(name="operating_margin", value=0.2614, format="percent", fiscal_period="FY2025", formula="f", provenance=prov),
            DerivedMetric(name="current_ratio", value=3.4245, format="ratio", fiscal_period="Q3 FY2026", formula="f", provenance=prov),
            DerivedMetric(name="ev_ebitda", value=61.17, format="x", fiscal_period="FY2025", formula="f", provenance=prov),
        ])


def test_number_grounded_handles_percent_and_ratio():
    d = _pct_ratio_dossier()
    assert is_number_grounded("FY2025 operating margin: 26.1%", d) is True   # 0.261 ~ 0.2614
    assert is_number_grounded("current ratio: 3.42x", d) is True             # 3.42 ~ 3.4245
    assert is_number_grounded("EV/EBITDA (FY25): 61x", d) is True            # 61 ~ 61.17
    assert is_number_grounded("margin of 99.9%", d) is False                 # ungrounded
    assert is_number_grounded("during FY2025", d) is False                   # bare year, no unit


def test_number_grounded_percentage_points_and_bps():
    from datetime import date
    from saturn.models import CompanyDossier, DerivedMetric, Provenance
    prov = Provenance(source="Saturn (model)")
    d = CompanyDossier(ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        derived_metrics=[
            DerivedMetric(name="expectations_gap", value=0.8356, format="percent", fiscal_period="model", formula="f", provenance=prov),
            DerivedMetric(name="spread", value=0.0038, format="percent", fiscal_period="model", formula="f", provenance=prov)])
    assert is_number_grounded("expectations gap of 84 percentage points", d) is True
    assert is_number_grounded("10Y-2Y spread of 38bps", d) is True


def test_number_grounded_uses_macro_series():
    from datetime import date
    from saturn.models import CompanyDossier, MacroSnapshot, MacroSeries, Provenance
    prov = Provenance(source="FRED")
    d = CompanyDossier(ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        macro=MacroSnapshot(series=[MacroSeries(series_id="VIXCLS", title="VIX",
            observations=[(date(2026, 7, 9), 15.84)], provenance=prov)]))
    assert is_number_grounded("low VIX at 15.8", d) is True     # macro value now in the corpus


class _ConfirmedNoiseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return ('{"claims_checked": 2, "summary": "s", "findings": ['
                '{"claim": "Cloud Memory BU +78% QoQ", "section": "business_segments",'
                ' "category": "unsupported_number", "verdict": "unsupported",'
                ' "evidence": "Calculations confirmed.", "severity": "low"},'
                '{"claim": "real mismatch", "section": "bull_thesis", "category": "contradiction",'
                ' "verdict": "contradicted", "evidence": "report says A, data shows B", "severity": "high"}]}')


def test_critique_drops_confirmed_noise_keeps_real_issue():
    review = critique(_analysis(), _debate(), _pct_ratio_dossier(), _ConfirmedNoiseLLM())
    assert [f.category for f in review.findings] == ["contradiction"]

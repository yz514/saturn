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


def test_number_not_grounded_on_bare_short_digit_in_source():
    """A fabricated '$2 billion' must NOT ground merely because the digit '2'
    appears somewhere in the filing text (e.g. 'Q2'). Regression: the source-text
    fallback used to accept any substring, so a blatantly wrong figure got dropped."""
    from datetime import date
    from saturn.models import CompanyDossier, FilingSection, Provenance
    d = CompanyDossier(ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        filing_sections=[FilingSection(name="Q2 results",
            excerpt="Q2 fiscal results were strong; segment 12 details follow.",
            provenance=Provenance(source="SEC EDGAR"))])
    assert is_number_grounded("revenue of only $2 billion", d) is False
    # A 2-digit bare figure is likewise too ambiguous to ground by substring.
    assert is_number_grounded("$12B in buybacks", d) is False


def test_number_grounded_requires_multidigit_source_match():
    """A specific 3+ significant-digit figure quoted verbatim still grounds."""
    from datetime import date
    from saturn.models import CompanyDossier, FilingSection, Provenance
    d = CompanyDossier(ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        filing_sections=[FilingSection(name="Cash flow",
            excerpt="adjusted free cash flow was $18.3 billion", provenance=Provenance(source="SEC EDGAR"))])
    assert is_number_grounded("$18.3B FCF", d) is True
    # ...but not when it is only a substring of a larger, different number.
    d2 = CompanyDossier(ticker="MU", name="Micron", generated_at=date(2026, 7, 10),
        filing_sections=[FilingSection(name="x", excerpt="the figure was $118.35 per unit",
            provenance=Provenance(source="SEC EDGAR"))])
    assert is_number_grounded("$18.3B", d2) is False


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


# ---- Critic-v2: self-repair helpers + revise() ----

from saturn.agents.critic import _actionable, _is_actionable_finding, _score, revise
from saturn.models import CriticFinding, CriticReview


def _rev(findings):
    return CriticReview(findings=findings, provenance=Provenance(source="Saturn (critic)"))


def _find(category="contradiction", severity="high", section="bear_thesis"):
    return CriticFinding(claim="c", section=section, category=category, verdict="v", evidence="e", severity=severity)


def test_score_is_severity_weighted():
    assert _score(_rev([_find(severity="high"), _find(severity="low")])) == 4  # 3 + 1


def test_actionable_matrix():
    assert _is_actionable_finding(_find("contradiction", "high")) is True
    assert _is_actionable_finding(_find("over_weighting", "medium")) is True
    assert _is_actionable_finding(_find("contradiction", "low")) is False       # low severity
    assert _is_actionable_finding(_find("unverified_claim", "high")) is False    # non-actionable category
    assert _actionable(_rev([_find("contradiction", "low"), _find("unsupported_number", "medium")])) is True
    assert _actionable(_rev([_find("unverified_claim", "high")])) is False


class _ReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=revise" in prompt
        return '{"bear_thesis": "corrected bear thesis"}'


class _BadReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return "not json"


def test_revise_returns_affected_section_corrections():
    corrections = revise(_analysis(), _debate(), _rev([_find("contradiction", "high", "bear_thesis")]),
                         _dossier(), _ReviseLLM())
    assert corrections == {"bear_thesis": "corrected bear thesis"}


def test_revise_no_actionable_findings_returns_none():
    assert revise(_analysis(), _debate(), _rev([_find("unverified_claim", "high", "bear_thesis")]),
                  _dossier(), _ReviseLLM()) is None


def test_revise_soft_fails_to_none():
    assert revise(_analysis(), _debate(), _rev([_find("contradiction", "high", "bear_thesis")]),
                  _dossier(), _BadReviseLLM()) is None


# ---- Alpha-framing: critic audits the alpha thesis ----

from saturn.agents.critic import _critic_prompt
from saturn.models import AlphaThesis, ExpectationAnchor, ScenarioLeg


def _alpha():
    return AlphaThesis(
        anchor=ExpectationAnchor(source="consensus", text="fwd P/E 6.5x", confidence="medium"),
        stance="above_consensus", variant="Market underrates HBM durability.", rationale="r",
        confidence="medium", key_variable="HBM GM", falsifier="GM<60% in 2Q", horizon="12-18m",
        scenarios=[ScenarioLeg(name="base", period="FY2027", driver="d", metric="EPS",
                   metric_basis="adjusted", per_share_value=10.0, multiple=15.0, multiple_basis="P/E")],
        provenance=Provenance(source="Saturn (synthesist)"))


def test_critic_prompt_includes_alpha_and_new_category():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=_alpha())
    assert "unsupported_alpha_inference" in p
    assert "Market underrates HBM durability." in p        # alpha thesis text is in the scan


def test_critic_prompt_omits_alpha_when_none():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=None)
    assert "unsupported_alpha_inference" not in p


class _AlphaInferenceLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return ('{"claims_checked": 1, "summary": "s", "findings": ['
                '{"claim": "margins reflect upfront recognition", "section": "alpha_thesis",'
                ' "category": "unsupported_alpha_inference", "verdict": "unsupported",'
                ' "evidence": "no contract-liability growth supports it", "severity": "high"}]}')


def test_critique_keeps_unsupported_alpha_inference():
    review = critique(_analysis(), _debate(), _dossier(), _AlphaInferenceLLM(), alpha=_alpha())
    assert [f.category for f in review.findings] == ["unsupported_alpha_inference"]


def test_critic_prompt_has_stance_vs_final_view_check():
    p = _critic_prompt(_analysis(), _debate(), "ctx", False, alpha=_alpha())
    assert "Final View" in p and "stance" in p.lower()


class _CaptureReviseLLM:
    """Records the section keys revise() offered for correction (parsed from the prompt)."""
    def __init__(self):
        self.offered_sections = None
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        import json, re
        m = re.search(r'CURRENT SECTION TEXT \(JSON\):\n(\{.*?\})\n\n', prompt, re.S)
        self.offered_sections = set(json.loads(m.group(1)).keys()) if m else set()
        return '{"bear_thesis": "corrected"}'


def test_revise_contradiction_widens_scope_to_all_sections():
    # A contradiction finding NAMED on executive_summary must let revise edit OTHER sections too
    # (the wrong value may live elsewhere — the MSFT RPO case).
    from saturn.agents.critic import revise
    review = _rev([_find("contradiction", "high", "executive_summary")])
    llm = _CaptureReviseLLM()
    revise(_analysis(), _debate(), review, _dossier(), llm)
    assert "bear_thesis" in llm.offered_sections          # a non-named section was offered
    assert "valuation_discussion" in llm.offered_sections


def test_revise_non_contradiction_stays_scoped_to_named_section():
    from saturn.agents.critic import revise
    review = _rev([_find("unsupported_number", "high", "financial_snapshot")])
    llm = _CaptureReviseLLM()
    revise(_analysis(), _debate(), review, _dossier(), llm)
    assert llm.offered_sections == {"financial_snapshot"}   # named-section-only scope preserved


def test_is_alpha_actionable_matrix():
    from saturn.agents.critic import _is_alpha_actionable, _alpha_actionable
    assert _is_alpha_actionable(_find("unsupported_alpha_inference", "high", "alpha_thesis")) is True
    assert _is_alpha_actionable(_find("contradiction", "medium", "alpha_thesis / final_view")) is True
    assert _is_alpha_actionable(_find("unsupported_alpha_inference", "low", "alpha_thesis")) is False
    assert _is_alpha_actionable(_find("contradiction", "high", "bull_thesis")) is False
    assert _alpha_actionable(_rev([_find("unsupported_alpha_inference", "high", "alpha_thesis")])) is True
    assert _alpha_actionable(_rev([_find("contradiction", "high", "bull_thesis")])) is False


class _AlphaReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=revise_alpha" in prompt
        # a corrected rationale PLUS stray derived keys that MUST be dropped
        return ('{"rationale": "corrected: ~3.9% 2-year FCF CAGR", '
                '"stance": "above_consensus", "scenarios": []}')


class _BadAlphaReviseLLM:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return "not json"


def test_revise_alpha_returns_prose_only():
    from saturn.agents.critic import revise_alpha
    findings = [_find("unsupported_alpha_inference", "high", "alpha_thesis")]
    corr = revise_alpha(_alpha(), _dossier(), findings, _AlphaReviseLLM())
    assert corr == {"rationale": "corrected: ~3.9% 2-year FCF CAGR"}   # stance/scenarios dropped


def test_revise_alpha_soft_fails_to_none():
    from saturn.agents.critic import revise_alpha
    findings = [_find("unsupported_alpha_inference", "high", "alpha_thesis")]
    assert revise_alpha(_alpha(), _dossier(), findings, _BadAlphaReviseLLM()) is None

"""Tests for saturn/agents/critic.py — grounding helper and critique()."""
from datetime import date

from saturn.agents.critic import is_dollar_grounded
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

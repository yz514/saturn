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

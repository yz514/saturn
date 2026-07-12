from datetime import date

from saturn.agents.guidance import extract_guidance
from saturn.models import CompanyDossier, FilingSection, FinancialFact, Fundamentals, Provenance

PROV = Provenance(source="SEC EDGAR")
_QUOTE = "We expect full-year revenue of approximately $70 billion."


def _dossier(quote_in_filing=True, rev=60_000_000_000.0):
    text = ("Some intro. " + _QUOTE + " More text.") if quote_in_filing else "No guidance here."
    return CompanyDossier(
        ticker="X", name="X", generated_at=date(2026, 7, 12),
        fundamentals=Fundamentals(facts=[
            FinancialFact(concept="Revenues", value=rev, unit="USD", fiscal_period="FY2025", provenance=PROV)]),
        filing_sections=[FilingSection(name="Earnings release", excerpt=text, provenance=PROV)])


class _GuidanceLLM:
    def __init__(self, payload):
        self.payload = payload
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        assert "OUTPUT_SCHEMA=guidance" in prompt
        return self.payload


def _fy_payload():
    return '{"value": 70000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'


def test_extract_guidance_grounded_fy():
    g = extract_guidance(_dossier(), _GuidanceLLM(_fy_payload()))
    assert g is not None and g.period == "FY"
    assert abs(g.implied_growth - (70_000_000_000 / 60_000_000_000 - 1)) < 1e-9  # ~0.1667


def test_extract_guidance_quarter_annualized():
    payload = '{"value": 20000000000, "period": "quarter", "quote": "We expect full-year revenue of approximately $70 billion."}'
    g = extract_guidance(_dossier(), _GuidanceLLM(payload))
    # quarter -> value*4 = 80B vs 60B TTM -> +0.333
    assert g is not None and abs(g.implied_growth - (80_000_000_000 / 60_000_000_000 - 1)) < 1e-9


def test_extract_guidance_ungrounded_quote_rejected():
    payload = '{"value": 70000000000, "period": "FY", "quote": "We expect revenue of $999 trillion."}'
    assert extract_guidance(_dossier(), _GuidanceLLM(payload)) is None


def test_extract_guidance_malformed_none():
    assert extract_guidance(_dossier(), _GuidanceLLM("not json")) is None


def test_extract_guidance_empty_object_none():
    assert extract_guidance(_dossier(), _GuidanceLLM("{}")) is None


def test_extract_guidance_absurd_growth_rejected():
    # value 700B vs 60B TTM -> +1066% -> out of bounds
    payload = '{"value": 700000000000, "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'
    assert extract_guidance(_dossier(), _GuidanceLLM(payload)) is None


def test_extract_guidance_no_filing_sections_none():
    from saturn.models import CompanyDossier, Fundamentals, FinancialFact
    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 7, 12),
                       fundamentals=Fundamentals(facts=[FinancialFact(
                           concept="Revenues", value=60e9, unit="USD", fiscal_period="FY2025", provenance=PROV)]),
                       filing_sections=[])
    assert extract_guidance(d, _GuidanceLLM(_fy_payload())) is None


def test_extract_guidance_non_numeric_value_none():
    payload = '{"value": "$70 billion", "period": "FY", "quote": "We expect full-year revenue of approximately $70 billion."}'
    assert extract_guidance(_dossier(), _GuidanceLLM(payload)) is None

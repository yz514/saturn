import pytest

from datetime import date

from saturn.ingestion.dossier import _mock_dossier
from saturn.llm.mock_client import MockLLMClient
from saturn.workflows.equity_research import (
    LLMResponseError,
    _MAX_OUTPUT_TOKENS,
    _company_context,
    analyze,
    debate,
    run,
)


def test_company_context_includes_inline_provenance():
    ctx = _company_context(_mock_dossier("NVDA"))
    assert "NVIDIA Corporation" in ctx
    # financial facts are rendered with their source
    assert "Revenues" in ctx
    assert "SEC EDGAR (mock)" in ctx
    # macro present with source
    assert "Federal Funds" in ctx


def test_run_accepts_dossier_and_builds_report():
    dossier = _mock_dossier("NVDA")
    report = run(dossier, MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.company is dossier
    assert report.company.quote.price == 900.0
    assert report.analysis.executive_summary
    assert report.debate.bull_thesis


def test_company_context_renders_gaps_block():
    from saturn.models import SourceGap

    dossier = _mock_dossier("NVDA")
    dossier.gaps = [SourceGap(source="fred", reason="fred adapter not configured")]
    ctx = _company_context(dossier)
    assert "DATA GAPS" in ctx
    assert "fred: fred adapter not configured" in ctx


def test_company_context_includes_material_events():
    from saturn.ingestion.dossier import _mock_dossier
    from saturn.workflows.equity_research import _company_context

    ctx = _company_context(_mock_dossier("NVDA"))
    assert "MATERIAL EVENTS" in ctx
    assert "Results of Operations and Financial Condition" in ctx
    # quarterly fact is rendered too (provenance-tagged fundamentals loop)
    assert "Q2 FY2025" in ctx


class _TruncatedClient:
    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        return '{"executive_summary": "abc'  # truncated JSON


class _CapturingClient:
    def __init__(self):
        self.calls = []

    def complete(self, system, prompt, *, model=None, max_tokens=2000):
        self.calls.append(max_tokens)
        if "OUTPUT_SCHEMA=debate" in prompt:
            return '{"bull_thesis": "b", "bear_thesis": "x", "final_view": "f"}'
        return (
            '{"executive_summary": "e", "company_overview": "c", '
            '"business_segments": "s", "financial_snapshot": "fs", '
            '"valuation_discussion": "v", "key_risks": "k", "open_questions": "o"}'
        )


def test_analyze_raises_llmresponseerror_on_truncated_json():
    with pytest.raises(LLMResponseError):
        analyze(_mock_dossier("NVDA"), _TruncatedClient())


def test_debate_raises_llmresponseerror_on_truncated_json():
    with pytest.raises(LLMResponseError):
        debate(_mock_dossier("NVDA"), _TruncatedClient())


def test_analyze_requests_max_output_tokens():
    client = _CapturingClient()
    analyze(_mock_dossier("NVDA"), client)
    assert client.calls == [_MAX_OUTPUT_TOKENS]


def test_debate_requests_max_output_tokens():
    client = _CapturingClient()
    debate(_mock_dossier("NVDA"), client)
    assert client.calls == [_MAX_OUTPUT_TOKENS]


from datetime import date

from saturn.models import (
    CompanyDossier,
    FilingSection,
    FinancialFact,
    Fundamentals,
    MaterialEvent,
    Provenance,
)
from saturn.workflows.equity_research import (
    _CTX_MAX_ANNUAL,
    _CTX_MAX_EVENTS,
    _CTX_SECTION_CHARS,
    _company_context,
)


def _big_dossier() -> CompanyDossier:
    prov = Provenance(source="SEC EDGAR")
    facts = []
    for fy in range(2019, 2026):  # 7 annual years of Revenues
        facts.append(FinancialFact(concept="Revenues", value=float(fy), unit="USD", fiscal_period=f"FY{fy}", provenance=prov))
    for i in range(1, 7):  # 6 quarters across FY2024/FY2025
        q = ((i - 1) % 4) + 1
        fy = 2024 if i <= 4 else 2025
        facts.append(FinancialFact(concept="Revenues", value=float(i), unit="USD", fiscal_period=f"Q{q} FY{fy}", provenance=prov))
    events = [
        MaterialEvent(filing_date=date(2025, m, 1), item_codes=["2.02"], title=f"Event {m}", excerpt="E" * 2000, provenance=prov)
        for m in range(1, 11)  # 10 events
    ]
    return CompanyDossier(
        ticker="NVDA",
        name="NVIDIA",
        fundamentals=Fundamentals(facts=facts),
        filing_sections=[FilingSection(name="Risk Factors", excerpt="R" * 5000, provenance=prov)],
        material_events=events,
        generated_at=date(2026, 6, 6),
    )


def test_context_caps_annual_facts():
    ctx = _company_context(_big_dossier())
    assert "FY2025" in ctx and "FY2024" in ctx and "FY2023" in ctx
    assert "FY2019" not in ctx and "FY2020" not in ctx
    assert _CTX_MAX_ANNUAL == 3


def test_context_trims_section_excerpt():
    ctx = _company_context(_big_dossier())
    run_of_r = max((len(s) for s in ctx.split() if set(s) == {"R"}), default=0)
    assert run_of_r <= _CTX_SECTION_CHARS


def test_context_caps_events():
    ctx = _company_context(_big_dossier())
    assert ctx.count("MATERIAL EVENTS") == 1
    event_lines = [ln for ln in ctx.splitlines() if ln.startswith("- ") and "Event " in ln]
    assert len(event_lines) <= _CTX_MAX_EVENTS

from datetime import date

from saturn.ingestion.dossier import _mock_dossier
from saturn.llm.mock_client import MockLLMClient
from saturn.workflows.equity_research import _company_context, run


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

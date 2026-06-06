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
    assert report.company.quote.price == 900.0
    assert report.analysis.executive_summary
    assert report.debate.bull_thesis

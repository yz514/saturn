from saturn.ingestion.dossier import _mock_dossier
from saturn.models import (
    AnalysisSections,
    DebateSections,
    ResearchReport,
)
from saturn.reports.markdown_report import render
from datetime import date


def _sample_report() -> ResearchReport:
    company = _mock_dossier("NVDA")
    analysis = AnalysisSections(
        executive_summary="ES",
        company_overview="CO",
        business_segments="BS",
        financial_snapshot="FS",
        valuation_discussion="VD",
        key_risks="KR",
        open_questions="OQ",
    )
    debate = DebateSections(bull_thesis="BULL", bear_thesis="BEAR", final_view="FV")
    return ResearchReport(
        ticker="NVDA",
        company=company,
        analysis=analysis,
        debate=debate,
        generated_at=date(2026, 5, 25),
        model_used="mock",
        mock=True,
        sources=["s1"],
    )


def test_render_has_all_thirteen_sections():
    md = render(_sample_report())
    expected = [
        "# NVDA Equity Research Report",
        "## 1. Executive Summary",
        "## 2. Company Overview",
        "## 3. Business Segments",
        "## 4. Recent Market Performance",
        "## 5. Financial Snapshot",
        "## 6. Recent News and Catalysts",
        "## 7. Bull Thesis",
        "## 8. Bear Thesis",
        "## 9. Key Risks",
        "## 10. Valuation Discussion",
        "## 11. Open Questions",
        "## 12. Final View",
        "## 13. Sources",
    ]
    for header in expected:
        assert header in md, f"missing: {header}"


def test_render_includes_disclaimer_and_content():
    md = render(_sample_report())
    assert "not investment advice" in md
    assert "BULL" in md and "BEAR" in md
    assert "[MOCK] NVIDIA announces next-gen architecture" in md
    assert "MOCK DATA" in md

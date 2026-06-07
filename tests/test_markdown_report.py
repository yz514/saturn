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


def test_render_has_all_sections():
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
        "## 13. Macro Snapshot",
        "## 14. Material Events (SEC 8-K)",
        "## 15. Sources",
    ]
    for header in expected:
        assert header in md, f"missing: {header}"


def test_render_includes_quote_and_financials_table():
    md = render(_sample_report())
    assert "# NVDA Equity Research Report" in md
    assert "$900" in md  # quote price humanized
    assert "Revenues" in md  # fundamentals table
    assert "FY2024" in md
    assert "Federal Funds Effective Rate" in md  # macro snapshot
    assert "_Source: yfinance (mock)_" in md  # quote source line
    assert "| Concept | Period | Value | Unit | Source |" in md  # 5-col table header


def test_render_includes_disclaimer_and_content():
    md = render(_sample_report())
    assert "not investment advice" in md
    assert "BULL" in md and "BEAR" in md
    assert "[MOCK] NVIDIA announces next-gen architecture" in md
    assert "MOCK DATA" in md


def test_render_shows_data_gaps_section():
    from saturn.models import SourceGap

    report = _sample_report()
    report.company.gaps = [SourceGap(source="edgar", reason="edgar adapter not configured")]
    md = render(report)
    assert "## 16. Data Gaps" in md
    assert "**edgar**: edgar adapter not configured" in md


def test_financial_table_is_bounded_per_concept():
    """The human report table shows only the most recent few periods per concept,
    not the full multi-year history the dossier holds."""
    from saturn.models import (
        CompanyDossier,
        FinancialFact,
        Fundamentals,
        Provenance,
    )

    prov = Provenance(source="SEC EDGAR")
    facts = []
    for fy in range(2018, 2026):  # 8 annual years
        facts.append(FinancialFact(concept="Revenues", value=float(fy), unit="USD", fiscal_period=f"FY{fy}", provenance=prov))
    for i in range(1, 7):  # 6 quarters across FY2024/FY2025
        q = ((i - 1) % 4) + 1
        fy = 2024 if i <= 4 else 2025
        facts.append(FinancialFact(concept="Revenues", value=float(i), unit="USD", fiscal_period=f"Q{q} FY{fy}", provenance=prov))
    dossier = CompanyDossier(
        ticker="NVDA", name="NVIDIA", fundamentals=Fundamentals(facts=facts), generated_at=date(2026, 6, 7)
    )

    report = _sample_report()
    report.company = dossier
    md = render(report)

    # most-recent annual + quarterly kept
    assert "FY2025" in md and "FY2024" in md and "FY2023" in md
    assert "Q2 FY2025" in md and "Q3 FY2024" in md
    # older periods dropped from the human table
    assert "FY2018" not in md and "FY2019" not in md
    assert "Q1 FY2024" not in md and "Q2 FY2024" not in md
    # transparency note about the bound (no silent truncation)
    assert "most recent" in md.lower()


def test_render_groups_financials_and_shows_events():
    md = render(_sample_report())  # uses _mock_dossier, has a quarterly fact + event
    assert "Q2 FY2025" in md                                   # quarterly row present
    assert md.index("FY2024") < md.index("Q2 FY2025")  # annual grouped before quarterly
    assert "## 14. Material Events (SEC 8-K)" in md            # new section
    assert "Results of Operations and Financial Condition" in md
    assert "## 15. Sources" in md                              # renumbered

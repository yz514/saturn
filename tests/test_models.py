from datetime import date

from saturn.models import (
    AnalysisSections,
    CompanyData,
    DebateSections,
    NewsItem,
    ResearchReport,
)


def test_company_data_minimal_defaults():
    c = CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))
    assert c.ticker == "NVDA"
    assert c.segments == []
    assert c.metrics == {}
    assert c.news == []


def test_research_report_composes_sections():
    company = CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))
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
    report = ResearchReport(
        ticker="NVDA",
        company=company,
        analysis=analysis,
        debate=debate,
        generated_at=date(2026, 5, 25),
        model_used="mock",
        mock=True,
        sources=["s1"],
    )
    assert report.analysis.key_risks == "KR"
    assert report.debate.final_view == "FV"
    assert report.mock is True


def test_news_item_optional_fields():
    n = NewsItem(title="Headline")
    assert n.title == "Headline"
    assert n.link is None

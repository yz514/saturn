from saturn.ingestion.dossier import _mock_dossier
from saturn.models import (
    AnalysisSections,
    DebateSections,
    FinancialFact,
    Provenance,
    ResearchReport,
)
from saturn.reports.markdown_report import _select_report_facts, render
from datetime import date


def _fact(concept, period, value):
    return FinancialFact(concept=concept, value=value, unit="USD", fiscal_period=period,
                         provenance=Provenance(source="SEC EDGAR"))


def test_select_report_facts_excludes_stale_concept():
    facts = [
        _fact("Revenues", "FY2025", 100.0), _fact("Revenues", "FY2024", 90.0),
        _fact("Revenues", "Q3 FY2026", 30.0),
        _fact("PropertyPlantAndEquipmentNet", "FY2019", 28.0),   # 6y stale
        _fact("PropertyPlantAndEquipmentNet", "Q3 FY2020", 30.0),
    ]
    kept, warnings = _select_report_facts(facts)
    kept_concepts = {f.concept for f in kept}
    assert "Revenues" in kept_concepts
    assert "PropertyPlantAndEquipmentNet" not in kept_concepts
    assert any(c == "PropertyPlantAndEquipmentNet" for c, _ in warnings)


def test_select_report_facts_keeps_fresh_concepts_no_warnings():
    facts = [_fact("Revenues", "FY2025", 100.0), _fact("Revenues", "FY2024", 90.0)]
    kept, warnings = _select_report_facts(facts)
    assert {f.concept for f in kept} == {"Revenues"} and warnings == []


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
        "## 1. Executive Summary",
        "## 2. Company Overview",
        "## 3. Business Segments",
        "## 4. Recent Market Performance",
        "## 5. Financial Snapshot",
        "## 6. Key Metrics",
        "## 7. Recent News and Catalysts",
        "## 8. Bull Thesis",
        "## 9. Bear Thesis",
        "## 10. Key Risks",
        "## 11. Valuation Discussion",
        "## 12. Open Questions",
        "## 13. Final View",
        "## 14. Verification (Critic)",
        "## 15. Macro Snapshot",
        "## 16. Material Events (SEC 8-K)",
        "## 17. Sources",
    ]
    for header in expected:
        assert header in md, f"missing: {header}"


def test_render_key_metrics_section():
    from saturn.models import DerivedMetric, MetricInput, Provenance
    report = _sample_report()
    report.company.derived_metrics = [
        DerivedMetric(name="net_margin", value=0.25, format="percent", fiscal_period="FY2024",
                      formula="NetIncomeLoss / Revenues",
                      inputs=[MetricInput(concept="NetIncomeLoss", fiscal_period="FY2024", value=1.0, source="SEC EDGAR")],
                      provenance=Provenance(source="Saturn (derived)")),
        DerivedMetric(name="pe_ratio", value=20.0, format="x", fiscal_period="TTM",
                      formula="market_cap / net_income_ttm",
                      inputs=[], provenance=Provenance(source="Saturn (derived)")),
    ]
    md = render(report)
    assert "## 6. Key Metrics" in md
    assert "net_margin" in md and "25.0%" in md          # percent formatting
    assert "20.0x" in md                                 # multiple formatting
    assert "docs/metrics.md" in md                       # methodology link


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
    assert "## 18. Data Gaps" in md
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


def test_key_metrics_table_shows_newest_periods_when_unordered():
    from saturn.models import DerivedMetric, Provenance
    report = _sample_report()
    prov = Provenance(source="Saturn (derived)")
    # deliberately out of order; only the 2 newest annual should render
    report.company.derived_metrics = [
        DerivedMetric(name="net_margin", value=0.10, format="percent", fiscal_period="FY2021", formula="NetIncomeLoss / Revenues", provenance=prov),
        DerivedMetric(name="net_margin", value=0.40, format="percent", fiscal_period="FY2024", formula="NetIncomeLoss / Revenues", provenance=prov),
        DerivedMetric(name="net_margin", value=0.20, format="percent", fiscal_period="FY2022", formula="NetIncomeLoss / Revenues", provenance=prov),
        DerivedMetric(name="net_margin", value=0.30, format="percent", fiscal_period="FY2023", formula="NetIncomeLoss / Revenues", provenance=prov),
    ]
    md = render(report)
    assert "FY2024" in md and "FY2023" in md      # newest 2 kept
    assert "FY2021" not in md and "FY2022" not in md  # older dropped


def test_render_groups_financials_and_shows_events():
    md = render(_sample_report())  # uses _mock_dossier, has a quarterly fact + event
    assert "Q2 FY2025" in md                                   # quarterly row present
    assert md.index("FY2024") < md.index("Q2 FY2025")  # annual grouped before quarterly
    assert "## 16. Material Events (SEC 8-K)" in md            # renumbered
    assert "Results of Operations and Financial Condition" in md
    assert "## 17. Sources" in md                              # renumbered


def _section7(md: str) -> str:
    return md.split("## 7. Recent News and Catalysts")[1].split("## 8.")[0]


def test_recent_news_falls_back_to_material_events_when_news_empty():
    report = _sample_report()
    report.company.news = []                       # no third-party news feed
    assert report.company.material_events          # mock dossier has an 8-K
    sec = _section7(render(report))
    assert "_No recent news available._" not in sec
    assert str(report.company.material_events[0].filing_date) in sec   # 8-K date listed
    assert "SEC 8-K filings" in sec                                    # source note


def test_recent_news_prefers_yfinance_news_when_present():
    report = _sample_report()
    assert report.company.news                      # mock has yfinance news
    sec = _section7(render(report))
    assert report.company.news[0].title in sec
    assert "SEC 8-K filings" not in sec             # events path not used


def test_recent_news_none_when_no_news_and_no_events():
    report = _sample_report()
    report.company.news = []
    report.company.material_events = []
    assert "_No recent news available._" in _section7(render(report))


def test_render_forward_expectations_subtable():
    from saturn.models import DerivedMetric, MetricInput, Provenance
    report = _sample_report()
    report.company.derived_metrics = [
        DerivedMetric(name="net_margin", value=0.25, format="percent", fiscal_period="FY2024",
                      formula="NetIncomeLoss / Revenues", provenance=Provenance(source="Saturn (derived)")),
        DerivedMetric(name="implied_fcf_growth", value=0.18, format="percent", fiscal_period="model",
                      formula="g s.t. 2-stage DCF(g, r=10%) = market_cap",
                      inputs=[MetricInput(concept="market_cap", value=1.0, source="yfinance")],
                      provenance=Provenance(source="Saturn (model)")),
        DerivedMetric(name="margin_of_safety", value=-0.30, format="percent", fiscal_period="model",
                      formula="reverse_dcf_fair_value (mid) / market_cap - 1",
                      provenance=Provenance(source="Saturn (model)")),
    ]
    md = render(report)
    assert "Forward / Expectations" in md
    assert "implied_fcf_growth" in md and "18.0%" in md
    assert "margin_of_safety" in md and "-30.0%" in md
    # the model metrics are NOT duplicated into the main Key Metrics table
    assert md.count("implied_fcf_growth") == 1
    # the main table still shows the derived metric
    assert "net_margin" in md
    assert "Low confidence" not in md   # normal case (MoS -30%, no clamp)


def test_render_forward_low_confidence_caveat():
    from saturn.models import DerivedMetric, MetricInput, Provenance
    report = _sample_report()
    report.company.derived_metrics = [
        DerivedMetric(name="implied_fcf_growth", value=0.60, format="percent", fiscal_period="model",
                      formula="g s.t. 2-stage DCF(g, r=10%) = market_cap",
                      inputs=[MetricInput(concept="implied_growth_clamped_to_bound", value=1.0, source="Saturn (model)")],
                      provenance=Provenance(source="Saturn (model)")),
    ]
    md = render(report)
    assert "Low confidence" in md and "cycle-depressed" in md


def test_render_consensus_subsection():
    from saturn.models import ConsensusSnapshot, Provenance
    report = _sample_report()
    report.company.consensus = ConsensusSnapshot(
        forward_pe=28.0, peg=1.5, target_mean=1000.0, target_upside_pct=0.11,
        rating="buy", n_analysts=40, last_eps_surprise_pct=0.05,
        provenance=Provenance(source="yfinance (estimate)"),
        rejected=["forward_eps: rejected — implies +266% vs verified trailing 4.80"],
    )
    md = render(report)
    assert "Consensus / Analyst Expectations" in md
    assert "28.0x" in md          # forward P/E
    assert "buy" in md and "40" in md
    assert "estimate" in md.lower()  # the best-effort caveat
    assert "rejected" in md and "forward_eps" in md  # rejection list surfaced


def test_render_consensus_absent():
    report = _sample_report()
    report.company.consensus = None
    md = render(report)
    assert "_No analyst consensus available._" in md


def test_render_consensus_all_rejected_still_shows_reasons():
    from saturn.models import ConsensusSnapshot, Provenance
    report = _sample_report()
    report.company.consensus = ConsensusSnapshot(
        provenance=Provenance(source="yfinance (estimate)"),
        rejected=["forward_eps/forward_pe/peg: rejected — forward_eps implies +306%"],
    )
    md = render(report)
    assert "all were rejected" in md
    assert "forward_eps" in md
    assert "_No analyst consensus available._" not in md


def test_render_verification_section():
    from saturn.models import CriticReview, CriticFinding, Provenance
    report = _sample_report()
    report.critic_review = CriticReview(
        findings=[CriticFinding(claim="Cloud fastest-growing", section="business_segments",
                  category="contradiction", verdict="contradicted", evidence="Core DC +653%", severity="high")],
        claims_checked=9, summary="1 issue.", provenance=Provenance(source="Saturn (critic)"))
    md = render(report)
    assert "Verification (Critic)" in md and "contradiction" in md and "Cloud fastest-growing" in md


def test_render_value_chain_subsection_present_when_industry_context():
    """render() includes ### Value-Chain / Demand Context and peer ticker when industry_context set."""
    from saturn.models import IndustryContext, PeerSummary, Provenance
    report = _sample_report()
    prov = Provenance(source="SEC EDGAR")
    report.company.industry_context = IndustryContext(
        peers=[PeerSummary(ticker="NVDA", role="demand", revenue_growth_yoy=1.22,
                           capex=11_000_000_000.0, provenance=prov)],
        note="test note",
        provenance=prov,
    )
    md = render(report)
    assert "### Value-Chain / Demand Context" in md
    assert "NVDA" in md


def test_render_value_chain_subsection_absent_without_industry_context():
    """render() does NOT include Value-Chain subsection when industry_context is None."""
    report = _sample_report()
    report.company.industry_context = None
    md = render(report)
    assert "Value-Chain / Demand Context" not in md


def test_render_verification_absent():
    report = _sample_report()
    report.critic_review = None
    assert "_Verification unavailable._" in render(report)

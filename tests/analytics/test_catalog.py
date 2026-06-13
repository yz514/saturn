from saturn.analytics.catalog import (
    METRIC_CATALOG,
    METRICS_DOC_PATH,
    MetricDef,
    render_metrics_reference,
)

CANONICAL = {
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin", "fcf_margin",
    "roe", "roa", "roic", "roce",
    "current_ratio", "quick_ratio", "cash_ratio",
    "debt_to_equity", "debt_to_assets", "net_debt", "net_debt_to_ebitda", "interest_coverage",
    "asset_turnover", "inventory_turnover", "capex_intensity", "days_sales_outstanding",
    "fcf", "fcf_conversion",
    "revenue_growth_yoy", "eps_growth_yoy", "fcf_growth_yoy",
    "revenue_cagr_3y", "eps_cagr_3y", "revenue_growth_qoq",
    "fcf_per_share", "book_value_per_share",
    "effective_tax_rate", "share_count_change_yoy", "dividend_coverage", "accruals_ratio",
    "revenue_ttm", "net_income_ttm", "eps_ttm",
    "pe_ratio", "ps_ratio", "pb_ratio", "p_fcf", "ev_ebitda", "ev_sales",
    "earnings_yield", "dividend_yield", "payout_ratio",
    "buyback_yield", "total_shareholder_yield",
}


def test_catalog_covers_canonical_names_exactly():
    assert set(METRIC_CATALOG) == CANONICAL


def test_catalog_entries_well_formed():
    valid_fmt = {"percent", "ratio", "currency", "x", "per_share"}
    for name, d in METRIC_CATALOG.items():
        assert isinstance(d, MetricDef)
        assert d.name == name
        assert d.fmt in valid_fmt
        assert d.formula and d.description


def test_docs_metrics_md_is_in_sync():
    # drift guard: the committed doc must equal the generated output
    committed = METRICS_DOC_PATH.read_text(encoding="utf-8")
    assert committed == render_metrics_reference()


def test_every_catalog_name_is_computable_and_vice_versa():
    from saturn.analytics.metrics import compute_metrics
    from saturn.models import FinancialFact, Fundamentals, Provenance, Quote

    prov = Provenance(source="SEC EDGAR")
    rows = []
    concepts = [
        "Revenues", "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss",
        "DepreciationAndAmortization", "StockholdersEquity", "Assets",
        "LiabilitiesCurrent", "AssetsCurrent", "Inventory",
        "CashAndCashEquivalents", "LongTermDebt", "DebtCurrent",
        "InterestExpense", "IncomeTaxExpenseBenefit", "CostOfRevenue",
        "CapitalExpenditures", "AccountsReceivableNetCurrent",
        "OperatingCashFlow", "WeightedAverageSharesDiluted",
        "EarningsPerShareDiluted", "DividendsPaid", "StockRepurchased",
    ]
    # Annual periods FY2022..FY2025 (CAGR/YoY) and 4 quarters of FY2025 (TTM/QoQ).
    for p in ["FY2022", "FY2023", "FY2024", "FY2025"]:
        for c in concepts:
            rows.append((c, p, 100.0 + len(c)))
    for q in ["Q1 FY2025", "Q2 FY2025", "Q3 FY2025", "Q4 FY2025"]:
        for c in concepts:
            rows.append((c, q, 50.0 + len(c)))
    fund = Fundamentals(facts=[
        FinancialFact(concept=c, value=v, unit="USD", fiscal_period=p, provenance=prov)
        for (c, p, v) in rows
    ])
    quote = Quote(price=100.0, market_cap=1_000_000.0, currency="USD", provenance=Provenance(source="yfinance"))

    produced = {m.name for m in compute_metrics(fund, quote)}
    assert produced == set(METRIC_CATALOG), (
        f"missing from compute: {set(METRIC_CATALOG) - produced}; "
        f"missing from catalog: {produced - set(METRIC_CATALOG)}"
    )

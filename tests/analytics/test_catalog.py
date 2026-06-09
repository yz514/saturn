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

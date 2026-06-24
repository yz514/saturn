"""Single source of truth for derived-metric metadata + reference-doc generator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

METRICS_DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "metrics.md"


@dataclass(frozen=True)
class MetricDef:
    name: str
    category: str
    fmt: str  # percent | ratio | currency | x | per_share
    formula: str
    description: str
    caveat: str | None = None


def _d(name, category, fmt, formula, description, caveat=None) -> MetricDef:
    return MetricDef(name, category, fmt, formula, description, caveat)


# Order here defines order in the generated doc.
_DEFS: list[MetricDef] = [
    # Profitability
    _d("gross_margin", "Profitability", "percent", "GrossProfit / Revenues", "Gross profit as a share of revenue."),
    _d("operating_margin", "Profitability", "percent", "OperatingIncomeLoss / Revenues", "Operating income as a share of revenue."),
    _d("net_margin", "Profitability", "percent", "NetIncomeLoss / Revenues", "Net income as a share of revenue."),
    _d("ebitda_margin", "Profitability", "percent", "(OperatingIncomeLoss + DepreciationAndAmortization) / Revenues", "EBITDA as a share of revenue.", "EBITDA approximated as operating income + D&A."),
    _d("fcf_margin", "Profitability", "percent", "(OperatingCashFlow - CapitalExpenditures) / Revenues", "Free cash flow as a share of revenue."),
    # Returns
    _d("roe", "Returns", "percent", "NetIncomeLoss / StockholdersEquity", "Return on equity.", "Annual periods only (period flow vs. point-in-time stock)."),
    _d("roa", "Returns", "percent", "NetIncomeLoss / Assets", "Return on assets.", "Annual periods only (period flow vs. point-in-time stock)."),
    _d("roic", "Returns", "percent", "(OperatingIncomeLoss * (1 - effective_tax_rate)) / (TotalDebt + StockholdersEquity)", "Return on invested capital.", "Annual only. NOPAT approx = operating income x (1 - effective tax rate); invested capital approx = total debt + equity."),
    _d("roce", "Returns", "percent", "OperatingIncomeLoss / (Assets - LiabilitiesCurrent)", "Return on capital employed.", "Annual periods only (period flow vs. point-in-time stock)."),
    # Liquidity
    _d("current_ratio", "Liquidity", "ratio", "AssetsCurrent / LiabilitiesCurrent", "Short-term assets vs short-term liabilities."),
    _d("quick_ratio", "Liquidity", "ratio", "(AssetsCurrent - Inventory) / LiabilitiesCurrent", "Acid-test liquidity."),
    _d("cash_ratio", "Liquidity", "ratio", "CashAndCashEquivalents / LiabilitiesCurrent", "Cash vs short-term liabilities."),
    # Leverage
    _d("debt_to_equity", "Leverage", "ratio", "TotalDebt / StockholdersEquity", "Leverage relative to equity.", "TotalDebt = LongTermDebt + DebtCurrent (LongTermDebt alone if DebtCurrent absent)."),
    _d("debt_to_assets", "Leverage", "ratio", "TotalDebt / Assets", "Leverage relative to assets."),
    _d("net_debt", "Leverage", "currency", "TotalDebt - CashAndCashEquivalents", "Debt net of cash."),
    _d("net_debt_to_ebitda", "Leverage", "x", "(TotalDebt - CashAndCashEquivalents) / (OperatingIncomeLoss + DepreciationAndAmortization)", "Years of EBITDA to repay net debt.", "Annual periods only (net debt is point-in-time; EBITDA is a period flow)."),
    _d("interest_coverage", "Leverage", "x", "OperatingIncomeLoss / InterestExpense", "Operating income vs interest expense."),
    # Efficiency
    _d("asset_turnover", "Efficiency", "x", "Revenues / Assets", "Revenue generated per dollar of assets.", "Annual periods only (period flow vs. point-in-time stock)."),
    _d("inventory_turnover", "Efficiency", "x", "CostOfRevenue / Inventory", "Cost of revenue vs inventory.", "Annual periods only (period flow vs. point-in-time stock)."),
    _d("capex_intensity", "Efficiency", "percent", "CapitalExpenditures / Revenues", "Capital spending as a share of revenue."),
    _d("days_sales_outstanding", "Efficiency", "ratio", "AccountsReceivableNetCurrent / Revenues * 365", "Average collection period (days), annual only."),
    # Cash
    _d("fcf", "Cash", "currency", "OperatingCashFlow - CapitalExpenditures", "Free cash flow."),
    _d("fcf_conversion", "Cash", "percent", "(OperatingCashFlow - CapitalExpenditures) / NetIncomeLoss", "How much net income converts to FCF (earnings quality)."),
    # Growth
    _d("revenue_growth_yoy", "Growth", "percent", "Revenues[t] / Revenues[t-1] - 1", "Year-over-year revenue growth."),
    _d("eps_growth_yoy", "Growth", "percent", "EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-1] - 1", "Year-over-year diluted EPS growth.", "Skipped across a split-like share-count change (>2x or <0.5x)."),
    _d("fcf_growth_yoy", "Growth", "percent", "FCF[t] / FCF[t-1] - 1", "Year-over-year FCF growth."),
    _d("revenue_cagr_3y", "Growth", "percent", "(Revenues[t] / Revenues[t-3]) ** (1/3) - 1", "3-year revenue CAGR.", "Only when both endpoints are positive."),
    _d("eps_cagr_3y", "Growth", "percent", "(EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-3]) ** (1/3) - 1", "3-year diluted EPS CAGR.", "Only when both endpoints are positive; skipped across a split-like share-count change."),
    _d("revenue_growth_qoq", "Growth", "percent", "Revenues[Q] / Revenues[Q-1] - 1", "Sequential quarter-over-quarter revenue growth."),
    # Per-share
    _d("fcf_per_share", "Per-share", "per_share", "(OperatingCashFlow - CapitalExpenditures) / WeightedAverageSharesDiluted", "Free cash flow per diluted share."),
    _d("book_value_per_share", "Per-share", "per_share", "StockholdersEquity / WeightedAverageSharesDiluted", "Book value per diluted share."),
    # Trailing-twelve-month
    _d("revenue_ttm", "Trailing-twelve-month", "currency", "sum(Revenues over last 4 single quarters)", "Trailing-twelve-month revenue."),
    _d("net_income_ttm", "Trailing-twelve-month", "currency", "sum(NetIncomeLoss over last 4 single quarters)", "Trailing-twelve-month net income."),
    _d("eps_ttm", "Trailing-twelve-month", "per_share", "sum(EarningsPerShareDiluted over last 4 single quarters)", "Trailing-twelve-month diluted EPS."),
    # Valuation
    _d("pe_ratio", "Valuation", "x", "market_cap / net_income_ttm", "Price/earnings (TTM, else latest FY)."),
    _d("ps_ratio", "Valuation", "x", "market_cap / revenue_ttm", "Price/sales (TTM, else latest FY)."),
    _d("pb_ratio", "Valuation", "x", "market_cap / StockholdersEquity", "Price/book."),
    _d("p_fcf", "Valuation", "x", "market_cap / FCF", "Price/free-cash-flow (latest FY)."),
    _d("ev_ebitda", "Valuation", "x", "(market_cap + net_debt) / EBITDA", "Enterprise value / EBITDA (latest FY).", "EV ignores leases, preferred, and minority interest."),
    _d("ev_sales", "Valuation", "x", "(market_cap + net_debt) / revenue_ttm", "Enterprise value / sales (TTM, else latest FY).", "EV ignores leases, preferred, and minority interest."),
    _d("earnings_yield", "Valuation", "percent", "net_income_ttm / market_cap", "Inverse of P/E."),
    _d("dividend_yield", "Valuation", "percent", "DividendsPaid / market_cap", "Trailing dividend yield (latest FY dividends)."),
    _d("payout_ratio", "Valuation", "percent", "DividendsPaid / NetIncomeLoss", "Dividends as a share of net income (latest FY)."),
    # Quality & capital return
    _d("effective_tax_rate", "Quality & capital return", "percent", "IncomeTaxExpenseBenefit / (NetIncomeLoss + IncomeTaxExpenseBenefit)", "Effective tax rate.", "Pretax income approximated as net income + tax expense."),
    _d("share_count_change_yoy", "Quality & capital return", "percent", "WeightedAverageSharesDiluted[t] / WeightedAverageSharesDiluted[t-1] - 1", "Diluted share-count change (dilution signal).", "Skipped when the change is split-like (>2x or <0.5x)."),
    _d("dividend_coverage", "Quality & capital return", "x", "(OperatingCashFlow - CapitalExpenditures) / DividendsPaid", "FCF coverage of dividends (latest FY)."),
    _d("accruals_ratio", "Quality & capital return", "percent", "(NetIncomeLoss - OperatingCashFlow) / Assets", "Accruals vs assets; high values flag lower earnings quality."),
    _d("buyback_yield", "Quality & capital return", "percent", "StockRepurchased / market_cap", "Buyback yield (latest FY repurchases)."),
    _d("total_shareholder_yield", "Quality & capital return", "percent", "(DividendsPaid + StockRepurchased) / market_cap", "Dividends + buybacks vs market cap (latest FY)."),
]

METRIC_CATALOG: dict[str, MetricDef] = {d.name: d for d in _DEFS}

_CATEGORY_ORDER = [
    "Profitability", "Returns", "Liquidity", "Leverage", "Efficiency", "Cash",
    "Growth", "Per-share", "Trailing-twelve-month", "Valuation",
    "Quality & capital return",
]


def render_metrics_reference() -> str:
    """Render METRIC_CATALOG to the canonical docs/metrics.md markdown."""
    lines = [
        "# Saturn Metric Definitions",
        "",
        "_Generated from `saturn/analytics/catalog.py` — do not edit by hand; "
        "run `saturn metrics --write` to regenerate._",
        "",
    ]
    by_cat: dict[str, list[MetricDef]] = {}
    for d in METRIC_CATALOG.values():
        by_cat.setdefault(d.category, []).append(d)
    for cat in _CATEGORY_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        lines += [f"## {cat}", "", "| Metric | Format | Formula | Notes |", "| --- | --- | --- | --- |"]
        for d in items:
            lines.append(f"| `{d.name}` | {d.fmt} | {d.formula} | {d.caveat or ''} |")
        lines.append("")
    return "\n".join(lines) + "\n"

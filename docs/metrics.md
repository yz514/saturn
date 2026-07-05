# Saturn Metric Definitions

_Generated from `saturn/analytics/catalog.py` — do not edit by hand; run `saturn metrics --write` to regenerate._

## Profitability

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `gross_margin` | percent | GrossProfit / Revenues |  |
| `operating_margin` | percent | OperatingIncomeLoss / Revenues |  |
| `net_margin` | percent | NetIncomeLoss / Revenues |  |
| `ebitda_margin` | percent | (OperatingIncomeLoss + DepreciationAndAmortization) / Revenues | EBITDA approximated as operating income + D&A. |
| `fcf_margin` | percent | (OperatingCashFlow - CapitalExpenditures) / Revenues |  |

## Returns

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `roe` | percent | NetIncomeLoss / StockholdersEquity | Annual periods only (period flow vs. point-in-time stock). |
| `roa` | percent | NetIncomeLoss / Assets | Annual periods only (period flow vs. point-in-time stock). |
| `roic` | percent | (OperatingIncomeLoss * (1 - effective_tax_rate)) / (TotalDebt + StockholdersEquity) | Annual only. NOPAT approx = operating income x (1 - effective tax rate); invested capital approx = total debt + equity. |
| `roce` | percent | OperatingIncomeLoss / (Assets - LiabilitiesCurrent) | Annual periods only (period flow vs. point-in-time stock). |

## Liquidity

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `current_ratio` | ratio | AssetsCurrent / LiabilitiesCurrent |  |
| `quick_ratio` | ratio | (AssetsCurrent - Inventory) / LiabilitiesCurrent |  |
| `cash_ratio` | ratio | CashAndCashEquivalents / LiabilitiesCurrent |  |

## Leverage

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `debt_to_equity` | ratio | TotalDebt / StockholdersEquity | TotalDebt = LongTermDebt + DebtCurrent (LongTermDebt alone if DebtCurrent absent). |
| `debt_to_assets` | ratio | TotalDebt / Assets |  |
| `net_debt` | currency | TotalDebt - CashAndCashEquivalents |  |
| `net_debt_to_ebitda` | x | (TotalDebt - CashAndCashEquivalents) / (OperatingIncomeLoss + DepreciationAndAmortization) | Annual periods only (net debt is point-in-time; EBITDA is a period flow). |
| `interest_coverage` | x | OperatingIncomeLoss / InterestExpense |  |

## Efficiency

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `asset_turnover` | x | Revenues / Assets | Annual periods only (period flow vs. point-in-time stock). |
| `inventory_turnover` | x | CostOfRevenue / Inventory | Annual periods only (period flow vs. point-in-time stock). |
| `capex_intensity` | percent | CapitalExpenditures / Revenues |  |
| `days_sales_outstanding` | ratio | AccountsReceivableNetCurrent / Revenues * 365 |  |

## Cash

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `fcf` | currency | OperatingCashFlow - CapitalExpenditures |  |
| `fcf_conversion` | percent | (OperatingCashFlow - CapitalExpenditures) / NetIncomeLoss |  |

## Growth

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `revenue_growth_yoy` | percent | Revenues[t] / Revenues[t-1] - 1 |  |
| `eps_growth_yoy` | percent | EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-1] - 1 | Skipped across a split-like share-count change (>2x or <0.5x). |
| `fcf_growth_yoy` | percent | FCF[t] / FCF[t-1] - 1 |  |
| `revenue_cagr_3y` | percent | (Revenues[t] / Revenues[t-3]) ** (1/3) - 1 | Only when both endpoints are positive. |
| `eps_cagr_3y` | percent | (EarningsPerShareDiluted[t] / EarningsPerShareDiluted[t-3]) ** (1/3) - 1 | Only when both endpoints are positive; skipped across a split-like share-count change. |
| `revenue_growth_qoq` | percent | Revenues[Q] / Revenues[Q-1] - 1 |  |

## Per-share

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `fcf_per_share` | per_share | (OperatingCashFlow - CapitalExpenditures) / WeightedAverageSharesDiluted |  |
| `book_value_per_share` | per_share | StockholdersEquity / WeightedAverageSharesDiluted |  |

## Trailing-twelve-month

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `revenue_ttm` | currency | Revenues: latest full FY + current-year YTD - prior-year YTD | Bridges the missing Q4 (no standalone Q4 10-Q); year-closed uses the annual. |
| `net_income_ttm` | currency | NetIncomeLoss: latest full FY + current-year YTD - prior-year YTD | Bridges the missing Q4 (no standalone Q4 10-Q); year-closed uses the annual. |
| `eps_ttm` | per_share | EarningsPerShareDiluted: latest full FY + current-year YTD - prior-year YTD | Approximate (per-period diluted EPS not perfectly additive); bridges the missing Q4. |

## Valuation

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `pe_ratio` | x | market_cap / net_income_ttm |  |
| `ps_ratio` | x | market_cap / revenue_ttm |  |
| `pb_ratio` | x | market_cap / StockholdersEquity |  |
| `p_fcf` | x | market_cap / FCF |  |
| `ev_ebitda` | x | (market_cap + net_debt) / EBITDA | EV ignores leases, preferred, and minority interest. |
| `ev_sales` | x | (market_cap + net_debt) / revenue_ttm | EV ignores leases, preferred, and minority interest. |
| `earnings_yield` | percent | net_income_ttm / market_cap |  |
| `dividend_yield` | percent | DividendsPaid / market_cap |  |
| `payout_ratio` | percent | DividendsPaid / NetIncomeLoss |  |

## Quality & capital return

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `effective_tax_rate` | percent | IncomeTaxExpenseBenefit / (NetIncomeLoss + IncomeTaxExpenseBenefit) | Pretax income approximated as net income + tax expense. |
| `share_count_change_yoy` | percent | WeightedAverageSharesDiluted[t] / WeightedAverageSharesDiluted[t-1] - 1 | Skipped when the change is split-like (>2x or <0.5x). |
| `dividend_coverage` | x | (OperatingCashFlow - CapitalExpenditures) / DividendsPaid |  |
| `accruals_ratio` | percent | (NetIncomeLoss - OperatingCashFlow) / Assets |  |
| `buyback_yield` | percent | StockRepurchased / market_cap |  |
| `total_shareholder_yield` | percent | (DividendsPaid + StockRepurchased) / market_cap |  |

## Forward / Expectations

| Metric | Format | Formula | Notes |
| --- | --- | --- | --- |
| `implied_fcf_growth` | percent | g s.t. 2-stage DCF(g, r=10%) = market_cap | 2-stage reverse-DCF on levered FCF (N=10, terminal 2.5%); FCFE-style (equity value vs market cap); clamped to search range [-50%, +60%]. |
| `expectations_gap` | percent | implied_fcf_growth - trailing_3y_FCF_CAGR | Positive = priced for acceleration; negative = priced below its track record. |
| `implied_return` | percent | r s.t. 2-stage DCF(our growth, r) = market_cap | Our growth = trailing 3-yr FCF CAGR clamped to [2.5%, 25%]. |
| `reverse_dcf_fair_value_per_share` | per_share | 2-stage DCF(our growth, r=10%) / diluted shares | Our growth = trailing 3-yr FCF CAGR clamped to [2.5%, 25%]; FCFE-style. Divides by diluted weighted-average shares; for the headline cheap/expensive read use margin_of_safety (total equity vs market cap), which can differ slightly since market cap reflects current shares outstanding. |
| `reverse_dcf_value_low_per_share` | per_share | 2-stage DCF(our growth, r=12%) / diluted shares | Higher discount rate. |
| `reverse_dcf_value_high_per_share` | per_share | 2-stage DCF(our growth, r=8%) / diluted shares | Lower discount rate. |
| `margin_of_safety` | percent | reverse_dcf_fair_value (mid) / market_cap - 1 | The headline cheap/expensive read: total mid-case equity value vs market cap (no share count). May differ a little from per-share fair value vs price, which divides by diluted weighted-average shares while market cap reflects current shares. |


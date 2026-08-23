import math
from typing import Any, Callable, Dict, List, Optional

RatioDef = Dict[str, Any]


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        val = float(str(v).replace(",", ""))
        return None if math.isnan(val) or math.isinf(val) else val
    except (ValueError, TypeError):
        return None


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    result = a / b
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def pct(v: Optional[float]) -> Optional[float]:
    return round(v * 100, 4) if v is not None else None


def ratio_field(tag: str) -> Callable[[Dict[str, Any]], Optional[float]]:
    return lambda d: to_float(d.get(tag))


def _compute_operating_margin(d: Dict[str, Any]) -> Optional[float]:
    pbt = to_float(d.get("profit_before_tax"))
    fc = to_float(d.get("finance_costs"))
    revenue = to_float(d.get("revenue_from_operations"))
    if pbt is None and fc is None:
        return None
    ebit = (pbt or 0) + (fc or 0)
    return pct(safe_div(ebit, revenue))


def _compute_roce(d: Dict[str, Any]) -> Optional[float]:
    pbt = to_float(d.get("profit_before_tax"))
    fc = to_float(d.get("finance_costs"))
    assets = to_float(d.get("assets"))
    ncl = to_float(d.get("noncurrent_liabilities"))
    if pbt is None and fc is None:
        return None
    ebit = (pbt or 0) + (fc or 0)
    capital_employed = (assets or 0) - (ncl or 0)
    return pct(safe_div(ebit, capital_employed))


def _compute_debt_to_equity(d: Dict[str, Any]) -> Optional[float]:
    direct = to_float(d.get("debt_equity_ratio"))
    if direct is not None and direct != 0:
        return direct
    debt = (to_float(d.get("borrowings_current")) or 0) + (
        to_float(d.get("borrowings_noncurrent")) or 0
    )
    equity = (to_float(d.get("equity_share_capital")) or 0) + (
        to_float(d.get("other_equity")) or 0
    )
    return safe_div(debt, equity)


Profitability: List[RatioDef] = [
    {
        "id": "net_profit_margin",
        "name": "Net Profit Margin",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("profit_loss_for_period")),
                to_float(d.get("revenue_from_operations")),
            )
        ),
    },
    {
        "id": "operating_margin",
        "name": "Operating Margin (EBIT / Revenue)",
        "compute": lambda d: _compute_operating_margin(d),
    },
    {
        "id": "pre_tax_margin",
        "name": "Pre-Tax Margin",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("profit_before_tax")),
                to_float(d.get("revenue_from_operations")),
            )
        ),
    },
    {
        "id": "interest_cost_ratio",
        "name": "Interest Cost Ratio",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("finance_costs")),
                to_float(d.get("revenue_from_operations")),
            )
        ),
    },
]

ReturnRatios: List[RatioDef] = [
    {
        "id": "roa",
        "name": "Return on Assets (ROA)",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("profit_loss_for_period")), to_float(d.get("assets"))
            )
        ),
    },
    {
        "id": "roe",
        "name": "Return on Equity (ROE)",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("profit_loss_for_period")),
                (to_float(d.get("equity_share_capital")) or 0)
                + (to_float(d.get("other_equity")) or 0),
            )
        ),
    },
    {
        "id": "roce",
        "name": "Return on Capital Employed (ROCE)",
        "compute": lambda d: _compute_roce(d),
    },
]

CapitalStructure: List[RatioDef] = [
    {
        "id": "debt_to_equity",
        "name": "Debt-to-Equity",
        "compute": lambda d: _compute_debt_to_equity(d),
    },
    {
        "id": "equity_ratio",
        "name": "Equity Ratio",
        "compute": lambda d: pct(
            safe_div(
                (to_float(d.get("equity_share_capital")) or 0)
                + (to_float(d.get("other_equity")) or 0),
                to_float(d.get("assets")),
            )
        ),
    },
    {
        "id": "financial_leverage",
        "name": "Financial Leverage",
        "compute": lambda d: safe_div(
            to_float(d.get("assets")),
            (to_float(d.get("equity_share_capital")) or 0)
            + (to_float(d.get("other_equity")) or 0),
        ),
    },
]

Liquidity: List[RatioDef] = [
    {
        "id": "cash_ratio",
        "name": "Cash Ratio",
        "compute": lambda d: safe_div(
            to_float(d.get("cash_and_cash_equivalents")),
            to_float(d.get("borrowings_current")),
        ),
    },
    {
        "id": "operating_cash_flow_ratio",
        "name": "Operating Cash Flow Ratio",
        "compute": lambda d: safe_div(
            to_float(d.get("cash_flows_from_used_in_operating_activities")),
            to_float(d.get("borrowings_current")),
        ),
    },
]

CashFlow: List[RatioDef] = [
    {
        "id": "operating_cash_flow_margin",
        "name": "Operating Cash Flow Margin",
        "compute": lambda d: pct(
            safe_div(
                to_float(d.get("cash_flows_from_used_in_operating_activities")),
                to_float(d.get("revenue_from_operations")),
            )
        ),
    },
    {
        "id": "ocf_to_net_income",
        "name": "OCF to Net Income",
        "compute": lambda d: safe_div(
            to_float(d.get("cash_flows_from_used_in_operating_activities")),
            to_float(d.get("profit_loss_for_period")),
        ),
    },
]

EarningsQuality: List[RatioDef] = [
    {
        "id": "dilution_impact",
        "name": "Dilution Impact",
        "compute": lambda d: pct(
            safe_div(
                (
                    to_float(
                        d.get(
                            "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                        )
                    )
                    or 0
                )
                - (
                    to_float(
                        d.get(
                            "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                        )
                    )
                    or 0
                ),
                to_float(
                    d.get(
                        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                    )
                ),
            )
        ),
    },
]

Growth: List[RatioDef] = [
    {"id": "revenue_growth_qoq", "name": "Revenue Growth (QoQ) %"},
    {"id": "revenue_growth_yoy", "name": "Revenue Growth (YoY) %"},
    {"id": "net_profit_growth_qoq", "name": "Net Profit Growth (QoQ) %"},
    {"id": "net_profit_growth_yoy", "name": "Net Profit Growth (YoY) %"},
    {"id": "eps_growth_qoq", "name": "EPS Growth (QoQ) %"},
    {"id": "eps_growth_yoy", "name": "EPS Growth (YoY) %"},
]

GROWTH_METRICS_MAP = {
    "revenue_growth": "revenue_from_operations",
    "net_profit_growth": "profit_loss_for_period",
    "eps_growth": "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
}


def compute_growth(
    current: Dict[str, Any], previous: Dict[str, Any]
) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for ratio_id, field in GROWTH_METRICS_MAP.items():
        curr_val = to_float(current.get(field))
        prev_val = to_float(previous.get(field))
        result[ratio_id] = pct(
            safe_div(
                curr_val - prev_val
                if curr_val is not None and prev_val is not None
                else None,
                prev_val,
            )
        )
    return result


def compute_static(d: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for category_name, ratios in ALL_CATEGORIES:
        category_result = {}
        for ratio in ratios:
            compute_fn = ratio.get("compute")
            if compute_fn:
                try:
                    category_result[ratio["id"]] = compute_fn(d)
                except Exception:
                    category_result[ratio["id"]] = None
        result[category_name] = category_result
    return result


def flatten_financials(financials_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in financials_list:
        tag = item["tag"]
        cr = item.get("contextRef") or ""
        is_quarterly = "OneI" in cr or "OneD" in cr
        if tag not in result:
            result[tag] = item["value"]
        elif is_quarterly:
            result[tag] = item["value"]
    return result


def extract_quarterly_value(
    financials: List[Dict[str, Any]], tag: str
) -> Optional[str]:
    """Extract a field preferring quarterly context (OneI/OneD) over annual (FourD)."""
    fallback = None
    for item in financials:
        if item["tag"] != tag:
            continue
        cr = item.get("contextRef", "")
        if "OneI" in cr or "OneD" in cr:
            return item["value"]
        if fallback is None:
            fallback = item["value"]
    return fallback


FinancialField = Dict[str, str]

FINANCIAL_FIELDS: List[FinancialField] = [
    {"id": "Symbol", "name": "Symbol", "type": "text", "category": "metadata"},
    {"id": "toDate", "name": "Period End Date", "type": "date", "category": "metadata"},
    {
        "id": "RevenueFromOperations",
        "name": "Revenue from Operations",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "OtherIncome",
        "name": "Other Income",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "Income",
        "name": "Total Income",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "FinanceCosts",
        "name": "Finance Costs",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "DepreciationDepletionAndAmortisationExpense",
        "name": "Depreciation, Depletion and Amortisation",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "OtherExpenses",
        "name": "Other Expenses",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "Expenses",
        "name": "Total Expenses",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitBeforeExceptionalItemsAndTax",
        "name": "Profit Before Exceptional Items & Tax",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ExceptionalItemsBeforeTax",
        "name": "Exceptional Items Before Tax",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitBeforeTax",
        "name": "Profit Before Tax (PBT)",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "CurrentTax",
        "name": "Current Tax",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "DeferredTax",
        "name": "Deferred Tax",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "TaxExpense",
        "name": "Total Tax Expense",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitLossForPeriodFromContinuingOperations",
        "name": "Profit/Loss from Continuing Operations",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitLossFromDiscontinuedOperationsBeforeTax",
        "name": "Profit/Loss from Discontinued Ops (Before Tax)",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "TaxExpenseOfDiscontinuedOperations",
        "name": "Tax on Discontinued Operations",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitLossFromDiscontinuedOperationsAfterTax",
        "name": "Profit/Loss from Discontinued Ops (After Tax)",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitLossForPeriod",
        "name": "Net Profit / Loss for Period",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ProfitOrLossAttributableToOwnersOfParent",
        "name": "Profit/Loss Attributable to Owners",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "ComprehensiveIncomeForThePeriod",
        "name": "Comprehensive Income for Period",
        "type": "currency",
        "category": "income_statement",
    },
    {
        "id": "PaidUpValueOfEquityShareCapital",
        "name": "Paid-up Equity Share Capital",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "FaceValueOfEquityShareCapital",
        "name": "Face Value per Share",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "EquityShareCapital",
        "name": "Equity Share Capital",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "OtherEquity",
        "name": "Other Equity (Reserves & Surplus)",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "DebtEquityRatio",
        "name": "Debt-to-Equity Ratio",
        "type": "ratio",
        "category": "balance_sheet",
    },
    {
        "id": "NoncurrentLiabilities",
        "name": "Non-current Liabilities",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "BorrowingsCurrent",
        "name": "Current Borrowings",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "NoncurrentInvestments",
        "name": "Non-current Investments",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "TradeReceivablesNoncurrent",
        "name": "Non-current Trade Receivables",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "LoansNoncurrent",
        "name": "Non-current Loans",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "OtherNoncurrentFinancialAssets",
        "name": "Other Non-current Financial Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "NoncurrentFinancialAssets",
        "name": "Non-current Financial Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "DeferredTaxAssetsNet",
        "name": "Deferred Tax Assets (Net)",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "OtherNoncurrentAssets",
        "name": "Other Non-current Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "NoncurrentAssets",
        "name": "Total Non-current Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "CapitalWorkInProgress",
        "name": "Capital Work in Progress (CWIP)",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "InvestmentProperty",
        "name": "Investment Property",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "Goodwill",
        "name": "Goodwill",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "OtherIntangibleAssets",
        "name": "Other Intangible Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "Assets",
        "name": "Total Assets",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "CashAndCashEquivalents",
        "name": "Cash & Cash Equivalents",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "CurrentLiabilities",
        "name": "Total Current Liabilities",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "BankBalanceOtherThanCashAndCashEquivalents",
        "name": "Bank Balance (Other than Cash)",
        "type": "currency",
        "category": "balance_sheet",
    },
    {
        "id": "CashFlowsFromUsedInOperations",
        "name": "Cash Flow from Operations",
        "type": "currency",
        "category": "cash_flow",
    },
    {
        "id": "CashFlowsFromUsedInOperatingActivities",
        "name": "Cash Flow from Operating Activities",
        "type": "currency",
        "category": "cash_flow",
    },
    {
        "id": "BasicEarningsLossPerShareFromContinuingOperations",
        "name": "Basic EPS from Continuing Operations",
        "type": "currency",
        "category": "per_share",
    },
    {
        "id": "DilutedEarningsLossPerShareFromContinuingOperations",
        "name": "Diluted EPS from Continuing Operations",
        "type": "currency",
        "category": "per_share",
    },
    {
        "id": "BasicEarningsLossPerShareFromDiscontinuedOperations",
        "name": "Basic EPS from Discontinued Operations",
        "type": "currency",
        "category": "per_share",
    },
    {
        "id": "DilutedEarningsLossPerShareFromDiscontinuedOperations",
        "name": "Diluted EPS from Discontinued Operations",
        "type": "currency",
        "category": "per_share",
    },
    {
        "id": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "name": "Basic EPS (Continuing + Discontinued)",
        "type": "currency",
        "category": "per_share",
    },
    {
        "id": "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "name": "Diluted EPS (Continuing + Discontinued)",
        "type": "currency",
        "category": "per_share",
    },
]

FINANCIAL_FIELD_MAP: Dict[str, FinancialField] = {f["id"]: f for f in FINANCIAL_FIELDS}

FINANCIAL_CATEGORIES: List[Dict[str, Any]] = [
    {"id": "metadata", "name": "Metadata", "type": "raw"},
    {"id": "income_statement", "name": "Income Statement", "type": "raw"},
    {"id": "balance_sheet", "name": "Balance Sheet", "type": "raw"},
    {"id": "cash_flow", "name": "Cash Flow", "type": "raw"},
    {"id": "per_share", "name": "Per Share Data", "type": "raw"},
]

RATIO_CATEGORIES: List[Dict[str, Any]] = [
    {"id": "profitability", "name": "Profitability Ratios", "type": "ratio"},
    {"id": "return", "name": "Return Ratios", "type": "ratio"},
    {"id": "capital_structure", "name": "Capital Structure Ratios", "type": "ratio"},
    {"id": "liquidity", "name": "Liquidity Ratios", "type": "ratio"},
    {"id": "cash_flow", "name": "Cash Flow Ratios", "type": "ratio"},
    {"id": "earnings_quality", "name": "Earnings Quality Ratios", "type": "ratio"},
]

RATIO_CATEGORY_MAP: Dict[str, Dict[str, Any]] = {c["id"]: c for c in RATIO_CATEGORIES}


def get_metrics_catalog() -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []

    for cat in FINANCIAL_CATEGORIES:
        metrics = [f for f in FINANCIAL_FIELDS if f["category"] == cat["id"]]
        if metrics:
            catalog.append(
                {
                    "id": f"raw_{cat['id']}",
                    "name": cat["name"],
                    "type": "raw",
                    "metrics": [
                        {"id": m["id"], "name": m["name"], "type": m["type"]}
                        for m in metrics
                    ],
                }
            )

    for cat_id, (_, ratios) in enumerate(ALL_CATEGORIES):
        cat_info = next(
            (c for c in RATIO_CATEGORIES if c["id"] == ALL_CATEGORIES[cat_id][0]), None
        )
        if not cat_info:
            continue
        catalog.append(
            {
                "id": f"ratio_{cat_info['id']}",
                "name": cat_info["name"],
                "type": "ratio",
                "metrics": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "type": "percentage"
                        if r["id"]
                        not in (
                            "debt_to_equity",
                            "financial_leverage",
                            "cash_ratio",
                            "operating_cash_flow_ratio",
                            "ocf_to_net_income",
                        )
                        else "multiple",
                    }
                    for r in ratios
                ],
            }
        )

    catalog.append(
        {
            "id": "ratio_growth",
            "name": "Growth Metrics",
            "type": "ratio",
            "metrics": [
                {"id": r["id"], "name": r["name"], "type": "percentage"} for r in Growth
            ],
        }
    )

    return catalog


ALL_CATEGORIES = [
    ("profitability", Profitability),
    ("return", ReturnRatios),
    ("capital_structure", CapitalStructure),
    ("liquidity", Liquidity),
    ("cash_flow", CashFlow),
    ("earnings_quality", EarningsQuality),
]

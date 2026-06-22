from typing import Any, Callable, Dict, List, Optional

RatioDef = Dict[str, Any]


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def pct(v: Optional[float]) -> Optional[float]:
    return round(v * 100, 4) if v is not None else None


def ratio_field(tag: str) -> Callable[[Dict[str, Any]], Optional[float]]:
    return lambda d: to_float(d.get(tag))


Profitability: List[RatioDef] = [
    {
        "id": "net_profit_margin",
        "name": "Net Profit Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitLossForPeriod")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "operating_margin",
        "name": "Operating Margin (approx)",
        "compute": lambda d: pct(safe_div(to_float(d.get("RevenueFromOperations")) - to_float(d.get("Expenses")) if to_float(d.get("RevenueFromOperations")) is not None and to_float(d.get("Expenses")) is not None else None, to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "pre_tax_margin",
        "name": "Pre-Tax Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitBeforeTax")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "pbt_margin",
        "name": "PBT Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitBeforeExceptionalItemsAndTax")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "comprehensive_income_margin",
        "name": "Comprehensive Income Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("ComprehensiveIncomeForThePeriod")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "continuing_operations_margin",
        "name": "Continuing Operations Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitLossForPeriodFromContinuingOperations")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "tax_rate",
        "name": "Tax Rate",
        "compute": lambda d: pct(safe_div(to_float(d.get("TaxExpense")), to_float(d.get("ProfitBeforeTax")))),
    },
    {
        "id": "effective_current_tax_rate",
        "name": "Effective Current Tax Rate",
        "compute": lambda d: pct(safe_div(to_float(d.get("CurrentTax")), to_float(d.get("ProfitBeforeTax")))),
    },
    {
        "id": "deferred_tax_rate",
        "name": "Deferred Tax Rate",
        "compute": lambda d: pct(safe_div(to_float(d.get("DeferredTax")), to_float(d.get("ProfitBeforeTax")))),
    },
    {
        "id": "interest_cost_ratio",
        "name": "Interest Cost Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("FinanceCosts")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "other_expense_ratio",
        "name": "Other Expense Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("OtherExpenses")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "other_income_ratio",
        "name": "Other Income Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("OtherIncome")), to_float(d.get("RevenueFromOperations")))),
    },
]

ReturnRatios: List[RatioDef] = [
    {
        "id": "roa",
        "name": "Return on Assets (ROA)",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitLossForPeriod")), to_float(d.get("Assets")))),
    },
    {
        "id": "roe",
        "name": "Return on Equity (ROE)",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitLossForPeriod")), (to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0))),
    },
    {
        "id": "roce",
        "name": "Return on Capital Employed (approx)",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitBeforeTax")), (to_float(d.get("Assets")) or 0) - (to_float(d.get("NoncurrentLiabilities")) or 0))),
    },
    {
        "id": "roic",
        "name": "Return on Invested Capital (simplified)",
        "compute": lambda d: pct(safe_div(to_float(d.get("ProfitBeforeTax")), (to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0) + (to_float(d.get("BorrowingsCurrent")) or 0))),
    },
]

CapitalStructure: List[RatioDef] = [
    {
        "id": "debt_to_equity",
        "name": "Debt-to-Equity",
        "compute": lambda d: to_float(d.get("DebtEquityRatio")),
    },
    {
        "id": "equity_ratio",
        "name": "Equity Ratio",
        "compute": lambda d: pct(safe_div((to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0), to_float(d.get("Assets")))),
    },
    {
        "id": "financial_leverage",
        "name": "Financial Leverage",
        "compute": lambda d: safe_div(to_float(d.get("Assets")), (to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0)),
    },
    {
        "id": "debt_ratio",
        "name": "Debt Ratio",
        "compute": lambda d: pct(safe_div((to_float(d.get("BorrowingsCurrent")) or 0) + (to_float(d.get("NoncurrentLiabilities")) or 0), to_float(d.get("Assets")))),
    },
    {
        "id": "borrowings_to_assets",
        "name": "Borrowings to Assets",
        "compute": lambda d: pct(safe_div(to_float(d.get("BorrowingsCurrent")), to_float(d.get("Assets")))),
    },
    {
        "id": "noncurrent_liabilities_to_assets",
        "name": "Noncurrent Liabilities to Assets",
        "compute": lambda d: pct(safe_div(to_float(d.get("NoncurrentLiabilities")), to_float(d.get("Assets")))),
    },
]

Liquidity: List[RatioDef] = [
    {
        "id": "cash_ratio",
        "name": "Cash Ratio",
        "compute": lambda d: safe_div(to_float(d.get("CashAndCashEquivalents")), to_float(d.get("BorrowingsCurrent"))),
    },
    {
        "id": "cash_bank_ratio",
        "name": "Cash + Bank Ratio",
        "compute": lambda d: safe_div((to_float(d.get("CashAndCashEquivalents")) or 0) + (to_float(d.get("BankBalanceOtherThanCashAndCashEquivalents")) or 0), to_float(d.get("BorrowingsCurrent"))),
    },
    {
        "id": "operating_cash_flow_ratio",
        "name": "Operating Cash Flow Ratio",
        "compute": lambda d: safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), to_float(d.get("BorrowingsCurrent"))),
    },
    {
        "id": "cash_to_assets",
        "name": "Cash to Assets",
        "compute": lambda d: pct(safe_div(to_float(d.get("CashAndCashEquivalents")), to_float(d.get("Assets")))),
    },
    {
        "id": "cash_to_equity",
        "name": "Cash to Equity",
        "compute": lambda d: pct(safe_div(to_float(d.get("CashAndCashEquivalents")), (to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0))),
    },
]

CashFlow: List[RatioDef] = [
    {
        "id": "operating_cash_flow_margin",
        "name": "Operating Cash Flow Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), to_float(d.get("RevenueFromOperations")))),
    },
    {
        "id": "ocf_to_net_income",
        "name": "Operating Cash Flow to Net Income",
        "compute": lambda d: safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), to_float(d.get("ProfitLossForPeriod"))),
    },
    {
        "id": "cash_conversion_of_earnings",
        "name": "Cash Conversion of Earnings",
        "compute": lambda d: safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), to_float(d.get("ProfitBeforeTax"))),
    },
    {
        "id": "cash_flow_to_assets",
        "name": "Cash Flow to Assets",
        "compute": lambda d: pct(safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), to_float(d.get("Assets")))),
    },
    {
        "id": "cash_flow_to_equity",
        "name": "Cash Flow to Equity",
        "compute": lambda d: pct(safe_div(to_float(d.get("CashFlowsFromUsedInOperatingActivities")), (to_float(d.get("EquityShareCapital")) or 0) + (to_float(d.get("OtherEquity")) or 0))),
    },
]

EarningsQuality: List[RatioDef] = [
    {
        "id": "eps_yield_on_equity_capital",
        "name": "EPS Yield on Equity Capital",
        "compute": lambda d: safe_div(to_float(d.get("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations")), to_float(d.get("FaceValueOfEquityShareCapital"))),
    },
    {
        "id": "dilution_impact",
        "name": "Dilution Impact",
        "compute": lambda d: pct(safe_div((to_float(d.get("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations")) or 0) - (to_float(d.get("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations")) or 0), to_float(d.get("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations")))),
    },
    {
        "id": "comprehensive_income_conversion",
        "name": "Comprehensive Income Conversion",
        "compute": lambda d: safe_div(to_float(d.get("ComprehensiveIncomeForThePeriod")), to_float(d.get("ProfitLossForPeriod"))),
    },
]

AssetComposition: List[RatioDef] = [
    {
        "id": "goodwill_ratio",
        "name": "Goodwill Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("Goodwill")), to_float(d.get("Assets")))),
    },
    {
        "id": "intangible_assets_ratio",
        "name": "Intangible Assets Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("OtherIntangibleAssets")), to_float(d.get("Assets")))),
    },
    {
        "id": "investment_property_ratio",
        "name": "Investment Property Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("InvestmentProperty")), to_float(d.get("Assets")))),
    },
    {
        "id": "noncurrent_assets_ratio",
        "name": "Noncurrent Assets Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("NoncurrentAssets")), to_float(d.get("Assets")))),
    },
    {
        "id": "cwip_ratio",
        "name": "CWIP Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("CapitalWorkInProgress")), to_float(d.get("Assets")))),
    },
    {
        "id": "investments_ratio",
        "name": "Investments Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("NoncurrentInvestments")), to_float(d.get("Assets")))),
    },
    {
        "id": "receivables_ratio",
        "name": "Receivables Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("TradeReceivablesNoncurrent")), to_float(d.get("Assets")))),
    },
]

Segment: List[RatioDef] = [
    {
        "id": "segment_margin",
        "name": "Segment Margin",
        "compute": lambda d: pct(safe_div(to_float(d.get("SegmentProfitBeforeTax")), to_float(d.get("SegmentRevenue")))),
    },
    {
        "id": "segment_asset_turnover",
        "name": "Segment Asset Turnover",
        "compute": lambda d: safe_div(to_float(d.get("SegmentRevenue")), to_float(d.get("SegmentAssets"))),
    },
    {
        "id": "segment_roa",
        "name": "Segment ROA",
        "compute": lambda d: pct(safe_div(to_float(d.get("SegmentProfitBeforeTax")), to_float(d.get("SegmentAssets")))),
    },
    {
        "id": "segment_liability_ratio",
        "name": "Segment Liability Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("SegmentLiabilities")), to_float(d.get("SegmentAssets")))),
    },
    {
        "id": "segment_finance_cost_ratio",
        "name": "Segment Finance Cost Ratio",
        "compute": lambda d: pct(safe_div(to_float(d.get("SegmentFinanceCosts")), to_float(d.get("SegmentRevenue")))),
    },
]

Growth: List[RatioDef] = [
    {
        "id": "revenue_growth",
        "name": "Revenue Growth %",
    },
    {
        "id": "net_profit_growth",
        "name": "Net Profit Growth %",
    },
    {
        "id": "eps_growth",
        "name": "EPS Growth %",
    },
    {
        "id": "asset_growth",
        "name": "Asset Growth %",
    },
    {
        "id": "equity_growth",
        "name": "Equity Growth %",
    },
    {
        "id": "cash_flow_growth",
        "name": "Cash Flow Growth %",
    },
    {
        "id": "other_income_growth",
        "name": "Other Income Growth %",
    },
]

GROWTH_METRICS_MAP = {
    "revenue_growth": "RevenueFromOperations",
    "net_profit_growth": "ProfitLossForPeriod",
    "eps_growth": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
    "asset_growth": "Assets",
    "equity_growth": "EquityShareCapital",
    "cash_flow_growth": "CashFlowsFromUsedInOperatingActivities",
    "other_income_growth": "OtherIncome",
}


def compute_growth(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for ratio_id, field in GROWTH_METRICS_MAP.items():
        curr_val = to_float(current.get(field))
        prev_val = to_float(previous.get(field))
        result[ratio_id] = pct(safe_div(curr_val - prev_val if curr_val is not None and prev_val is not None else None, prev_val))
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
        result[item["tag"]] = item["value"]
    return result


FinancialField = Dict[str, str]

FINANCIAL_FIELDS: List[FinancialField] = [
    {"id": "Symbol", "name": "Symbol", "type": "text", "category": "metadata"},
    {"id": "toDate", "name": "Period End Date", "type": "date", "category": "metadata"},
    {"id": "RevenueFromOperations", "name": "Revenue from Operations", "type": "currency", "category": "income_statement"},
    {"id": "OtherIncome", "name": "Other Income", "type": "currency", "category": "income_statement"},
    {"id": "Income", "name": "Total Income", "type": "currency", "category": "income_statement"},
    {"id": "FinanceCosts", "name": "Finance Costs", "type": "currency", "category": "income_statement"},
    {"id": "OtherExpenses", "name": "Other Expenses", "type": "currency", "category": "income_statement"},
    {"id": "Expenses", "name": "Total Expenses", "type": "currency", "category": "income_statement"},
    {"id": "ProfitBeforeExceptionalItemsAndTax", "name": "Profit Before Exceptional Items & Tax", "type": "currency", "category": "income_statement"},
    {"id": "ExceptionalItemsBeforeTax", "name": "Exceptional Items Before Tax", "type": "currency", "category": "income_statement"},
    {"id": "ProfitBeforeTax", "name": "Profit Before Tax (PBT)", "type": "currency", "category": "income_statement"},
    {"id": "CurrentTax", "name": "Current Tax", "type": "currency", "category": "income_statement"},
    {"id": "DeferredTax", "name": "Deferred Tax", "type": "currency", "category": "income_statement"},
    {"id": "TaxExpense", "name": "Total Tax Expense", "type": "currency", "category": "income_statement"},
    {"id": "ProfitLossForPeriodFromContinuingOperations", "name": "Profit/Loss from Continuing Operations", "type": "currency", "category": "income_statement"},
    {"id": "ProfitLossFromDiscontinuedOperationsBeforeTax", "name": "Profit/Loss from Discontinued Ops (Before Tax)", "type": "currency", "category": "income_statement"},
    {"id": "TaxExpenseOfDiscontinuedOperations", "name": "Tax on Discontinued Operations", "type": "currency", "category": "income_statement"},
    {"id": "ProfitLossFromDiscontinuedOperationsAfterTax", "name": "Profit/Loss from Discontinued Ops (After Tax)", "type": "currency", "category": "income_statement"},
    {"id": "ProfitLossForPeriod", "name": "Net Profit / Loss for Period", "type": "currency", "category": "income_statement"},
    {"id": "ProfitOrLossAttributableToOwnersOfParent", "name": "Profit/Loss Attributable to Owners", "type": "currency", "category": "income_statement"},
    {"id": "ComprehensiveIncomeForThePeriod", "name": "Comprehensive Income for Period", "type": "currency", "category": "income_statement"},
    {"id": "PaidUpValueOfEquityShareCapital", "name": "Paid-up Equity Share Capital", "type": "currency", "category": "balance_sheet"},
    {"id": "FaceValueOfEquityShareCapital", "name": "Face Value per Share", "type": "currency", "category": "balance_sheet"},
    {"id": "EquityShareCapital", "name": "Equity Share Capital", "type": "currency", "category": "balance_sheet"},
    {"id": "OtherEquity", "name": "Other Equity (Reserves & Surplus)", "type": "currency", "category": "balance_sheet"},
    {"id": "DebtEquityRatio", "name": "Debt-to-Equity Ratio", "type": "ratio", "category": "balance_sheet"},
    {"id": "NoncurrentLiabilities", "name": "Non-current Liabilities", "type": "currency", "category": "balance_sheet"},
    {"id": "BorrowingsCurrent", "name": "Current Borrowings", "type": "currency", "category": "balance_sheet"},
    {"id": "NoncurrentInvestments", "name": "Non-current Investments", "type": "currency", "category": "balance_sheet"},
    {"id": "TradeReceivablesNoncurrent", "name": "Non-current Trade Receivables", "type": "currency", "category": "balance_sheet"},
    {"id": "LoansNoncurrent", "name": "Non-current Loans", "type": "currency", "category": "balance_sheet"},
    {"id": "OtherNoncurrentFinancialAssets", "name": "Other Non-current Financial Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "NoncurrentFinancialAssets", "name": "Non-current Financial Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "DeferredTaxAssetsNet", "name": "Deferred Tax Assets (Net)", "type": "currency", "category": "balance_sheet"},
    {"id": "OtherNoncurrentAssets", "name": "Other Non-current Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "NoncurrentAssets", "name": "Total Non-current Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "CapitalWorkInProgress", "name": "Capital Work in Progress (CWIP)", "type": "currency", "category": "balance_sheet"},
    {"id": "InvestmentProperty", "name": "Investment Property", "type": "currency", "category": "balance_sheet"},
    {"id": "Goodwill", "name": "Goodwill", "type": "currency", "category": "balance_sheet"},
    {"id": "OtherIntangibleAssets", "name": "Other Intangible Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "Assets", "name": "Total Assets", "type": "currency", "category": "balance_sheet"},
    {"id": "CashAndCashEquivalents", "name": "Cash & Cash Equivalents", "type": "currency", "category": "balance_sheet"},
    {"id": "BankBalanceOtherThanCashAndCashEquivalents", "name": "Bank Balance (Other than Cash)", "type": "currency", "category": "balance_sheet"},
    {"id": "CashFlowsFromUsedInOperations", "name": "Cash Flow from Operations", "type": "currency", "category": "cash_flow"},
    {"id": "CashFlowsFromUsedInOperatingActivities", "name": "Cash Flow from Operating Activities", "type": "currency", "category": "cash_flow"},
    {"id": "SegmentRevenueFromOperations", "name": "Segment Revenue from Operations", "type": "currency", "category": "segment"},
    {"id": "SegmentProfitLossBeforeTaxAndFinanceCosts", "name": "Segment PBT before Finance Costs", "type": "currency", "category": "segment"},
    {"id": "SegmentFinanceCosts", "name": "Segment Finance Costs", "type": "currency", "category": "segment"},
    {"id": "SegmentRevenue", "name": "Segment Revenue", "type": "currency", "category": "segment"},
    {"id": "InterSegmentRevenue", "name": "Inter-Segment Revenue", "type": "currency", "category": "segment"},
    {"id": "SegmentProfitBeforeTax", "name": "Segment Profit Before Tax", "type": "currency", "category": "segment"},
    {"id": "SegmentAssets", "name": "Segment Assets", "type": "currency", "category": "segment"},
    {"id": "UnAllocableAssets", "name": "Unallocable Assets", "type": "currency", "category": "segment"},
    {"id": "NetSegmentAssets", "name": "Net Segment Assets", "type": "currency", "category": "segment"},
    {"id": "SegmentLiabilities", "name": "Segment Liabilities", "type": "currency", "category": "segment"},
    {"id": "UnAllocableLiabilities", "name": "Unallocable Liabilities", "type": "currency", "category": "segment"},
    {"id": "NetSegmentLiabilities", "name": "Net Segment Liabilities", "type": "currency", "category": "segment"},
    {"id": "BasicEarningsLossPerShareFromContinuingOperations", "name": "Basic EPS from Continuing Operations", "type": "currency", "category": "per_share"},
    {"id": "DilutedEarningsLossPerShareFromContinuingOperations", "name": "Diluted EPS from Continuing Operations", "type": "currency", "category": "per_share"},
    {"id": "BasicEarningsLossPerShareFromDiscontinuedOperations", "name": "Basic EPS from Discontinued Operations", "type": "currency", "category": "per_share"},
    {"id": "DilutedEarningsLossPerShareFromDiscontinuedOperations", "name": "Diluted EPS from Discontinued Operations", "type": "currency", "category": "per_share"},
    {"id": "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "name": "Basic EPS (Continuing + Discontinued)", "type": "currency", "category": "per_share"},
    {"id": "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "name": "Diluted EPS (Continuing + Discontinued)", "type": "currency", "category": "per_share"},
]

FINANCIAL_FIELD_MAP: Dict[str, FinancialField] = {f["id"]: f for f in FINANCIAL_FIELDS}

FINANCIAL_CATEGORIES: List[Dict[str, Any]] = [
    {"id": "metadata", "name": "Metadata", "type": "raw"},
    {"id": "income_statement", "name": "Income Statement", "type": "raw"},
    {"id": "balance_sheet", "name": "Balance Sheet", "type": "raw"},
    {"id": "cash_flow", "name": "Cash Flow", "type": "raw"},
    {"id": "per_share", "name": "Per Share Data", "type": "raw"},
    {"id": "segment", "name": "Segment Data", "type": "raw"},
]

RATIO_CATEGORIES: List[Dict[str, Any]] = [
    {"id": "profitability", "name": "Profitability Ratios", "type": "ratio"},
    {"id": "return", "name": "Return Ratios", "type": "ratio"},
    {"id": "capital_structure", "name": "Capital Structure Ratios", "type": "ratio"},
    {"id": "liquidity", "name": "Liquidity Ratios", "type": "ratio"},
    {"id": "cash_flow", "name": "Cash Flow Ratios", "type": "ratio"},
    {"id": "earnings_quality", "name": "Earnings Quality Ratios", "type": "ratio"},
    {"id": "asset_composition", "name": "Asset Composition Ratios", "type": "ratio"},
    {"id": "segment", "name": "Segment Ratios", "type": "ratio"},
]

RATIO_CATEGORY_MAP: Dict[str, Dict[str, Any]] = {c["id"]: c for c in RATIO_CATEGORIES}


def get_metrics_catalog() -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []

    for cat in FINANCIAL_CATEGORIES:
        metrics = [f for f in FINANCIAL_FIELDS if f["category"] == cat["id"]]
        if metrics:
            catalog.append({
                "id": f"raw_{cat['id']}",
                "name": cat["name"],
                "type": "raw",
                "metrics": [
                    {"id": m["id"], "name": m["name"], "type": m["type"]}
                    for m in metrics
                ],
            })

    for cat_id, (_, ratios) in enumerate(ALL_CATEGORIES):
        cat_info = next((c for c in RATIO_CATEGORIES if c["id"] == ALL_CATEGORIES[cat_id][0]), None)
        if not cat_info:
            continue
        catalog.append({
            "id": f"ratio_{cat_info['id']}",
            "name": cat_info["name"],
            "type": "ratio",
            "metrics": [
                {"id": r["id"], "name": r["name"], "type": "percentage" if r["id"] not in ("debt_to_equity", "financial_leverage", "segment_asset_turnover", "cash_ratio", "cash_bank_ratio", "operating_cash_flow_ratio", "ocf_to_net_income", "cash_conversion_of_earnings", "eps_yield_on_equity_capital", "comprehensive_income_conversion") else "multiple"}
                for r in ratios
            ],
        })

    catalog.append({
        "id": "ratio_growth",
        "name": "Growth Metrics",
        "type": "ratio",
        "metrics": [
            {"id": r["id"], "name": r["name"], "type": "percentage"}
            for r in Growth
        ],
    })

    return catalog


ALL_CATEGORIES = [
    ("profitability", Profitability),
    ("return", ReturnRatios),
    ("capital_structure", CapitalStructure),
    ("liquidity", Liquidity),
    ("cash_flow", CashFlow),
    ("earnings_quality", EarningsQuality),
    ("asset_composition", AssetComposition),
    ("segment", Segment),
]

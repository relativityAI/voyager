from typing import Any, Optional

# ---------------------------------------------------------------------------
# Style palette — white-shades primary, restrained accent colours.
#   bright white (bold)  -> headers, headlines, primary values
#   plain white          -> ordinary values
#   dim (grey)           -> labels, metadata, secondary information
#   green / red          -> only where the sign of a number carries meaning
# ---------------------------------------------------------------------------
HEADER = "bold white"
VALUE = "white"
LABEL = "dim"
ACCENT = "cyan"
POS = "green"
NEG = "red"
WARN = "yellow"
ERR = "red"
MUTED = "grey37"

# Metrics whose values are plain ratios (not rupee amounts / percentages).
RATIO_PLAIN = {"debt_equity_ratio"}

MONEY_KEYS = {
    "current_price",
    "high_52w",
    "low_52w",
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_20",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "atr_14",
    "market_capitalization",
    "enterprise_value",
    "total_debt",
    "total_equity",
    "cash_and_equivalents",
    "earnings_per_share",
    "book_value_per_share",
    "free_cash_flow_per_share",
}

PCT_KEYS = {
    "revenue_growth",
    "earnings_growth",
    "book_value_growth",
    "earnings_per_share_growth",
    "free_cash_flow_growth",
    "operating_income_growth",
    "ebitda_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "return_on_assets",
    "return_on_invested_capital",
    "free_cash_flow_yield",
    "payout_ratio",
    "change_pct",
    "delivery_percentage",
}

VOLUME_KEYS = {"volume", "avg_volume_10d", "avg_volume_3m"}

RATIO_KEYS = {
    "price_to_earnings_ratio",
    "price_to_book_ratio",
    "price_to_sales_ratio",
    "enterprise_value_to_ebitda_ratio",
    "enterprise_value_to_revenue_ratio",
    "peg_ratio",
    "debt_to_equity",
    "debt_to_assets",
    "interest_coverage",
    "asset_turnover",
    "inventory_turnover",
    "receivables_turnover",
    "days_sales_outstanding",
    "operating_cycle",
    "working_capital_turnover",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "operating_cash_flow_ratio",
    "volume_ratio",
}

METRIC_LABELS = {
    "current_price": "Current Price",
    "rsi_14": "RSI (14)",
    "sma_20": "SMA (20)",
    "sma_50": "SMA (50)",
    "sma_200": "SMA (200)",
    "ema_20": "EMA (20)",
    "bb_upper": "Bollinger Upper",
    "bb_middle": "Bollinger Middle",
    "bb_lower": "Bollinger Lower",
    "atr_14": "ATR (14)",
    "volume": "Volume",
    "avg_volume_10d": "Avg Volume (10d)",
    "avg_volume_3m": "Avg Volume (3m)",
    "high_52w": "52W High",
    "low_52w": "52W Low",
    "change_pct": "Change %",
    "volume_ratio": "Volume Ratio",
    "delivery_percentage": "Delivery %",
    "relative_strength": "Relative Strength",
    "market_capitalization": "Market Cap",
    "enterprise_value": "Enterprise Value",
    "price_to_earnings_ratio": "P/E",
    "price_to_book_ratio": "P/B",
    "price_to_sales_ratio": "P/S",
    "enterprise_value_to_ebitda_ratio": "EV/EBITDA",
    "enterprise_value_to_revenue_ratio": "EV/Revenue",
    "free_cash_flow_yield": "FCF Yield",
    "peg_ratio": "PEG",
    "gross_margin": "Gross Margin",
    "operating_margin": "Operating Margin",
    "net_margin": "Net Margin",
    "return_on_equity": "ROE",
    "return_on_assets": "ROA",
    "return_on_invested_capital": "ROIC",
    "asset_turnover": "Asset Turnover",
    "inventory_turnover": "Inventory Turnover",
    "receivables_turnover": "Receivables Turnover",
    "days_sales_outstanding": "Days Sales Outstanding",
    "operating_cycle": "Operating Cycle",
    "working_capital_turnover": "Working Capital Turnover",
    "current_ratio": "Current Ratio",
    "quick_ratio": "Quick Ratio",
    "cash_ratio": "Cash Ratio",
    "operating_cash_flow_ratio": "OCF Ratio",
    "debt_to_equity": "Debt / Equity",
    "debt_to_assets": "Debt / Assets",
    "interest_coverage": "Interest Coverage",
    "revenue_growth": "Revenue Growth",
    "earnings_growth": "Earnings Growth",
    "book_value_growth": "Book Value Growth",
    "earnings_per_share_growth": "EPS Growth",
    "free_cash_flow_growth": "FCF Growth",
    "operating_income_growth": "Operating Income Growth",
    "ebitda_growth": "EBITDA Growth",
    "earnings_per_share": "EPS",
    "book_value_per_share": "Book Value / Share",
    "free_cash_flow_per_share": "FCF / Share",
    "payout_ratio": "Payout Ratio",
    "total_debt": "Total Debt",
    "total_equity": "Total Equity",
    "cash_and_equivalents": "Cash & Equivalents",
    "period_end_date": "Period End",
}

METRIC_GROUPS = {
    "Market": [
        "current_price",
        "rsi_14",
        "sma_20",
        "sma_50",
        "sma_200",
        "ema_20",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "atr_14",
        "volume",
        "avg_volume_10d",
        "avg_volume_3m",
        "high_52w",
        "low_52w",
        "change_pct",
        "volume_ratio",
        "delivery_percentage",
        "relative_strength",
    ],
    "Valuation": [
        "market_capitalization",
        "enterprise_value",
        "price_to_earnings_ratio",
        "price_to_book_ratio",
        "price_to_sales_ratio",
        "enterprise_value_to_ebitda_ratio",
        "enterprise_value_to_revenue_ratio",
        "free_cash_flow_yield",
        "peg_ratio",
    ],
    "Profitability": [
        "gross_margin",
        "operating_margin",
        "net_margin",
        "return_on_equity",
        "return_on_assets",
        "return_on_invested_capital",
    ],
    "Efficiency": [
        "asset_turnover",
        "inventory_turnover",
        "receivables_turnover",
        "days_sales_outstanding",
        "operating_cycle",
        "working_capital_turnover",
    ],
    "Liquidity": [
        "current_ratio",
        "quick_ratio",
        "cash_ratio",
        "operating_cash_flow_ratio",
    ],
    "Solvency": ["debt_to_equity", "debt_to_assets", "interest_coverage"],
    "Growth": [
        "revenue_growth",
        "earnings_growth",
        "book_value_growth",
        "earnings_per_share_growth",
        "free_cash_flow_growth",
        "operating_income_growth",
        "ebitda_growth",
    ],
    "Per Share": [
        "earnings_per_share",
        "book_value_per_share",
        "free_cash_flow_per_share",
    ],
    "Other": [
        "payout_ratio",
    ],
}


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", ""))
        return None if (f != f or abs(f) == float("inf")) else f
    except (ValueError, TypeError):
        return None


def fmt_amount(v) -> Optional[str]:
    f = _to_float(v)
    if f is None:
        return None
    sign = "-" if f < 0 else ""
    x = abs(f)
    if x >= 1e14:
        return f"{sign}₹{x / 1e14:.2f} L Cr"
    if x >= 1e7:
        return f"{sign}₹{x / 1e7:.2f} Cr"
    if x >= 1e5:
        return f"{sign}₹{x / 1e5:.2f} L"
    if x >= 1e3:
        return f"{sign}₹{x:,.0f}"
    return f"{sign}₹{x:,.2f}"


def fmt_pct(v) -> Optional[str]:
    f = _to_float(v)
    return f"{f:+.2f}%" if f is not None else None


def fmt_num(v) -> Optional[str]:
    f = _to_float(v)
    if f is None:
        return None
    sign = "-" if f < 0 else ""
    x = abs(f)
    if x >= 1e12:
        return f"{sign}{x / 1e12:.2f}T"
    if x >= 1e9:
        return f"{sign}{x / 1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}{x / 1e6:.2f}M"
    if x >= 1e3:
        return f"{sign}{x / 1e3:.1f}K"
    return f"{sign}{x:,.2f}"


def fmt_metric_value(key: str, v) -> Any:
    """Format a financial-metrics value; returns a rich Text with colour."""
    from rich.text import Text

    f = _to_float(v)
    if f is None:
        return Text("—", style=LABEL)
    if key in PCT_KEYS:
        s = fmt_pct(f)
        style = POS if f > 0 else NEG if f < 0 else VALUE
    elif key in MONEY_KEYS:
        s = fmt_amount(f)
        style = VALUE
    elif key in VOLUME_KEYS:
        s = fmt_num(f)
        style = VALUE
    else:
        s = f"{f:,.2f}"
        style = NEG if f < 0 else VALUE
    return Text(s, style=style)


def humanize_key(key: str) -> str:
    return key.replace("_", " ").strip().title()

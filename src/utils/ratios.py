from typing import List, Optional, Union

import pandas as pd
import requests


def safe_div(
    numerator: Union[float, int], denominator: Union[float, int]
) -> Optional[float]:
    try:
        return round(100 * (numerator / denominator), 2)
    except Exception:
        return None


def cagr(start: float, end: float, periods: int) -> Optional[float]:
    if periods <= 0 or start is None or end is None or start <= 0:
        return None
    return (end / start) ** (1 / periods) - 1.0


def ebitda_margin(
    total_revenue: float,
    other_income: float,
    total_expense: float,
    finance_cost: float,
    depreciation_amort: float,
) -> Optional[float]:
    oper_revenue = total_revenue - other_income
    oper_expense = total_expense - finance_cost - depreciation_amort
    ebitda = oper_revenue - oper_expense
    return safe_div(ebitda, oper_revenue)


def pat_margin(net_profit: float, total_revenue: float) -> Optional[float]:
    return safe_div(net_profit, total_revenue)


def ebitda_growth(
    ebitda_start: float, ebitda_end: float, periods: int
) -> Optional[float]:
    return cagr(ebitda_start, ebitda_end, periods)


def pat_growth(pat_start: float, pat_end: float, periods: int) -> Optional[float]:
    return cagr(pat_start, pat_end, periods)


def return_on_equity(
    net_profit: float, avg_shareholders_equity: float
) -> Optional[float]:
    return safe_div(net_profit, avg_shareholders_equity)


def return_on_assets(
    net_profit: float,
    finance_cost: float,
    avg_total_assets: float,
    tax_rate: float = 0.32,
) -> Optional[float]:
    interest_after_tax = finance_cost * (1 - tax_rate)
    numerator = net_profit + interest_after_tax
    return safe_div(numerator, avg_total_assets)


def return_on_capital_employed(
    ebit: float, total_debt: float, shareholders_equity: float
) -> Optional[float]:
    capital_employed = total_debt + shareholders_equity
    return safe_div(ebit, capital_employed)


### Leverage ratios


def interest_coverage(ebit: float, interest_expense: float) -> Optional[float]:
    try:
        return round((ebit / interest_expense), 2)
    except Exception:
        return None


def debt_to_equity(total_debt: float, shareholders_equity: float) -> Optional[float]:
    return safe_div(total_debt, shareholders_equity)


def debt_to_asset(total_debt: float, total_assets: float) -> Optional[float]:
    return safe_div(total_debt, total_assets)


def financial_leverage(
    total_assets: float, shareholders_equity: float
) -> Optional[float]:
    try:
        return round((total_assets / shareholders_equity), 2)
    except Exception:
        return None


### Operating (efficiency / turnover) ratios


def fixed_assets_turnover(sales: float, avg_fixed_assets: float) -> Optional[float]:
    try:
        return round((sales / avg_fixed_assets), 2)
    except Exception:
        return None


def working_capital_turnover(
    sales: float, avg_working_capital: float
) -> Optional[float]:
    return safe_div(sales, avg_working_capital)


def total_assets_turnover(sales: float, avg_total_assets: float) -> Optional[float]:
    return safe_div(sales, avg_total_assets)


def inventory_turnover(
    cost_of_goods_sold: float, avg_inventory: float
) -> Optional[float]:
    return safe_div(cost_of_goods_sold, avg_inventory)


def inventory_number_of_days(
    avg_inventory: float, cost_of_goods_sold: float
) -> Optional[float]:
    inv_turn = inventory_turnover(cost_of_goods_sold, avg_inventory)
    if inv_turn is None or inv_turn == 0:
        return None
    return 365.0 / inv_turn


def receivable_turnover_ratio(sales: float, avg_receivables: float) -> Optional[float]:
    return safe_div(sales, avg_receivables)


def days_sales_outstanding(avg_receivables: float, sales: float) -> Optional[float]:
    rec_turn = receivable_turnover_ratio(sales, avg_receivables)
    if rec_turn is None or rec_turn == 0:
        return None
    return 365.0 / rec_turn


def price_to_sales(
    current_price: float, total_revenue: float, total_shares: float
) -> Optional[float]:
    sales_per_share = safe_div(total_revenue, total_shares)
    return safe_div(current_price, sales_per_share)


def price_to_book_value(
    current_price: float, book_value_per_share: float
) -> Optional[float]:
    return safe_div(current_price, book_value_per_share)


def price_to_earnings(
    current_price: float, earnings_per_share: float
) -> Optional[float]:
    try:
        return round((current_price / earnings_per_share), 2)
    except Exception:
        return None


# ___for_admin____calculate_and_store_all__ratios______


def fetch_raw_results(
    symbol: str = "KPITTECH",
    results_endpoint="http://localhost:8002/results",
    period="annual",
    consolidated=True,
    filter_keys: Optional[Union[str, List[str]]] = [
        "RevenueFromOperations",
        "Income",
        "OtherIncome",
        "ProfitLossForPeriod",
        "ProfitBeforeTax",
        "DilutedEarningsLossPerShareFromContinuingOperations",
        "Expenses",
        "FinanceCosts",
        "DepreciationDepletionAndAmortisationExpense",
        "ExceptionalItemsBeforeTax",
        "NetSegmentAssets",
        "PaidUpValueOfEquityShareCapital",
        "ReserveExcludingRevaluationReserves",
        "CashAndCashEquivalents",
        "BorrowingsNoncurrent",
        "BorrowingsCurrent",
        "CurrentAssets",
        "OtherCurrentAssets",
        "NoncurrentAssets",
        "OtherNoncurrentAssets",
        "CurrentLiabilities",
        "OtherCurrentLiabilities",
        "NoncurrentLiabilities",
        "OtherNoncurrentLiabilities",
        "CashFlowsFromUsedInOperatingActivities",
    ],
    filtered=True,
    preprocess=True,
    from_date=None,
    to_date=None,
):

    params = {
        "symbol": symbol,
        "period": period,
        "filter_keys": filter_keys,
        "filtered": filtered,
        "from_date": from_date,
        "to_date": to_date,
    }
    data = requests.get(results_endpoint, params=params).json()
    df = pd.DataFrame(data)
    df = df.pivot_table(
        index=["consolidated", "symbol", "toDate"],  # keep these as identifiers
        columns="tag",  # make tag values into columns
        values="value",  # fill with values
        aggfunc="first",  # in case duplicates exist, take the first
    ).reset_index()

    if preprocess:
        # Some preprocessing
        df["toDate"] = pd.to_datetime(df["toDate"])
        df = df.sort_values(by="toDate", ascending=False)

        if consolidated:
            df = df[df["consolidated"].str.lower() == "consolidated"]
        else:
            df = df[df["consolidated"].str.lower() != "consolidated"]

        if period == "annual":
            df = df[df["toDate"].dt.month == 3]

        for key in filter_keys:
            if key in df.columns:
                df[key] = df[key].astype("float")

        # Reaming for convenience
        df = df.rename(
            columns={
                "toDate": "date",
                "ProfitLossForPeriod": "pat",
                "RevenueFromOperations": "revenue",
                "FinanceCosts": "interest",
                "CashAndCashEquivalents": "cash",
                "CashFlowsFromUsedInOperatingActivities": "cfo",
            }
        )
        df = df.sort_values("date")  # ensure chronological
    return df


def process_raw_results(
    df,
    period="quarterly",
):

    df = df.sort_values(by="date", ascending=False)

    # margins
    df["ebitda_margin"] = df.apply(
        lambda row: ebitda_margin(
            row["revenue"],
            row["OtherIncome"],
            row["Expenses"],
            row["interest"],
            row["DepreciationDepletionAndAmortisationExpense"],
        ),
        axis=1,
    )
    df["pat_margin"] = df.apply(
        lambda row: pat_margin(row["pat"], row["revenue"]), axis=1
    )

    # growth & roe & roce
    df["ebitda"] = df["ebitda_margin"] * df["revenue"] / 100
    df["ebit"] = df["ProfitBeforeTax"] + df["interest"]
    # df['ebit'] = df['ebitda'] + df['DepreciationDepletionAndAmortisationExpense']

    # ttm

    def calc_ttm(raw_col: str, final_col: str):
        df[final_col] = (
            df[raw_col].iloc[::-1].rolling(4).sum().iloc[::-1]
            if period == "quarterly"
            else df[raw_col]
        )

    # df["pat_ttm"] = df["pat"].iloc[::-1].rolling(4).sum().iloc[::-1]

    calc_ttm("DilutedEarningsLossPerShareFromContinuingOperations", "eps_ttm")
    calc_ttm("revenue", "revenue_ttm")
    calc_ttm("pat", "pat_ttm")
    calc_ttm("ebitda", "ebitda_ttm")
    calc_ttm("ebit", "ebit_ttm")

    # for leverage ratios
    df["debt"] = df["BorrowingsCurrent"] + df["BorrowingsNoncurrent"]
    df["current_assets"] = df["CurrentAssets"] + df["OtherCurrentAssets"]
    df["non_current_assets"] = df["NoncurrentAssets"] + df["OtherNoncurrentAssets"]

    df["current_liabilities"] = df["CurrentLiabilities"] + df["OtherCurrentLiabilities"]
    df["non_current_liabilities"] = (
        df["NoncurrentLiabilities"] + df["OtherNoncurrentLiabilities"]
    )

    try:
        df["equity"] = (
            df["PaidUpValueOfEquityShareCapital"]
            + df["ReserveExcludingRevaluationReserves"]
        )
    except:
        df["equity"] = pd.NA

    return df


def calc_fundamental_ratios(
    df: pd.DataFrame,
    period="quarterly",
    # consolidated=True
):

    if period == "quarterly":
        df["revenue_growth_qoq"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["revenue"].shift(-1), df["revenue"])
        ]
        df["ebitda_growth_qoq"] = [
            round(100 * ebitda_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["ebitda"].shift(-1), df["ebitda"])
        ]
        df["pat_growth_qoq"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["pat"].shift(-1), df["pat"])
        ]

        df["revenue_growth_yoy"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["revenue"].shift(-4), df["revenue"])
        ]
        df["ebitda_growth_yoy"] = [
            round(100 * ebitda_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["ebitda"].shift(-4), df["ebitda"])
        ]
        df["pat_growth_yoy"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["pat"].shift(-4), df["pat"])
        ]

        for n_period in [1, 3, 5, 10]:
            df[f"revenue_growth_{n_period}y"] = [
                round(100 * ebitda_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(
                    df["revenue_ttm"].shift(-n_period * 4), df["revenue_ttm"]
                )
            ]
            df[f"ebitda_growth_{n_period}y"] = [
                round(100 * ebitda_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(
                    df["ebitda_ttm"].shift(-n_period * 4), df["ebitda_ttm"]
                )
            ]
            df[f"pat_growth_{n_period}y"] = [
                round(100 * pat_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(df["pat_ttm"].shift(-n_period * 4), df["pat_ttm"])
            ]
    else:
        df["revenue_growth_yoy"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["revenue"].shift(-1), df["revenue"])
        ]
        df["ebitda_growth_yoy"] = [
            round(100 * ebitda_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["ebitda"].shift(-1), df["ebitda"])
        ]
        df["pat_growth_yoy"] = [
            round(100 * pat_growth(start, end, 1), 2)
            if pd.notnull(start) and pd.notnull(end)
            else None
            for start, end in zip(df["pat"].shift(-1), df["pat"])
        ]

        for n_period in [1, 3, 5, 10]:
            df[f"revenue_growth_{n_period}y"] = [
                round(100 * ebitda_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(
                    df["revenue_ttm"].shift(-n_period), df["revenue_ttm"]
                )
            ]
            df[f"ebitda_growth_{n_period}y"] = [
                round(100 * ebitda_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(
                    df["ebitda_ttm"].shift(-n_period), df["ebitda_ttm"]
                )
            ]
            df[f"pat_growth_{n_period}y"] = [
                round(100 * pat_growth(start, end, n_period), 2)
                if pd.notnull(start) and pd.notnull(end)
                else None
                for start, end in zip(df["pat_ttm"].shift(-n_period), df["pat_ttm"])
            ]

    df["return_on_equity"] = df.apply(
        lambda row: return_on_equity(row["pat"], row["equity"]), axis=1
    )
    df["return_on_assets"] = df.apply(
        lambda row: round(
            return_on_assets(row["pat"], row["interest"], row["NetSegmentAssets"]), 2
        ),
        axis=1,
    )

    # leverage ratios
    df["interest_coverage"] = df.apply(
        lambda row: interest_coverage(row["ebit"], row["interest"]), axis=1
    )
    df["financial_leverage"] = df.apply(
        lambda row: financial_leverage(row["NetSegmentAssets"], row["equity"]), axis=1
    )

    # operating ratios
    df["assets_turnover"] = df.apply(
        lambda row: round(
            financial_leverage(row["revenue"], row["NetSegmentAssets"]), 2
        ),
        axis=1,
    )

    # FINAL FILTER
    final_ratios = [
        "date",
        "ebitda_margin",
        "pat_margin",
        "revenue_growth_yoy",
        "ebitda_growth_yoy",
        "pat_growth_yoy",
        "ebitda_growth_1y",
        "ebitda_growth_3y",
        "ebitda_growth_5y",
        "ebitda_growth_10y",
        "pat_growth_1y",
        "pat_growth_3y",
        "pat_growth_5y",
        "pat_growth_10y",
        "revenue_growth_1y",
        "revenue_growth_3y",
        "revenue_growth_5y",
        "revenue_growth_10y",
        "return_on_assets",
        "return_on_equity",
        "assets_turnover",
        "financial_leverage",
        "interest_coverage",
    ]

    if period == "quarterly":
        final_ratios.extend(
            [
                "revenue_growth_qoq",
                "ebitda_growth_qoq",
                "pat_growth_qoq",
            ]
        )

    fundamentals_df = df[final_ratios].dropna(how="all", axis=1)
    fundamentals_df["date"] = fundamentals_df["date"].dt.strftime("%Y-%m-%d")
    return fundamentals_df


def calc_valuations_time_series(df: pd.DataFrame, prices_df):

    # flatten multiIndex if present
    if isinstance(prices_df.index, pd.MultiIndex):
        prices_df = prices_df.droplevel("Ticker")

    prices_df = prices_df.reset_index()  # make 'Date' a column
    prices_df = prices_df.sort_values("date")  # must be sorted

    # eps df
    eps_df = df[
        [
            "date",
            "eps_ttm",
            "pat",
            "pat_ttm",
            "revenue_ttm",
            "debt",
            "cash",
            "ebitda_ttm",
        ]
    ].copy()
    eps_df["date"] = pd.to_datetime(eps_df["date"])
    eps_df = eps_df.sort_values("date")  # must be sorted for merge_asof

    # merge prices and eps
    valuation_df = pd.merge_asof(
        prices_df,
        eps_df,
        on="date",
        direction="backward",  # take last EPS at or before price date
    )

    # Optional: forward fill if you want the EPS to continue after last report
    valuation_df["eps_ttm"] = valuation_df["eps_ttm"].ffill()
    valuation_df["revenue_ttm"] = valuation_df["revenue_ttm"].ffill()
    valuation_df["pat_ttm"] = valuation_df["pat_ttm"].ffill()
    valuation_df["debt"] = valuation_df["debt"].ffill()
    valuation_df["cash"] = valuation_df["cash"].ffill()

    valuation_df["shares_outstanding"] = (
        valuation_df["pat_ttm"] / valuation_df["eps_ttm"]
    )
    valuation_df["market_cap"] = (
        valuation_df["price"] * valuation_df["shares_outstanding"]
    )
    valuation_df["enterprise_value"] = (
        valuation_df["market_cap"] + valuation_df["debt"] - valuation_df["cash"]
    )

    valuation_df["price_to_earnings"] = valuation_df.apply(
        lambda row: price_to_earnings(row["price"], row["eps_ttm"]), axis=1
    )
    valuation_df["price_to_sales"] = valuation_df.apply(
        lambda row: price_to_sales(
            row["price"], row["revenue_ttm"], row["shares_outstanding"]
        ),
        axis=1,
    )
    valuation_df["ev_ebitda"] = valuation_df.apply(
        lambda row: round(row["enterprise_value"] / row["ebitda_ttm"], 2), axis=1
    )

    final_metrics = [
        "date",
        "price_to_earnings",
        "price_to_sales",
        "ev_ebitda",
    ]

    valuation_df = valuation_df[final_metrics].dropna(how="all", axis=1)
    valuation_df["date"] = valuation_df["date"].dt.strftime("%Y-%m-%d")
    return valuation_df


def fetch_prices_from_db():
    pass

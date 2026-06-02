from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# This file contains the Pydantic model for the Screener tool response.
# Other tool response schemas should be added as separate files in src/models/
# or added here if they are closely related.


class ScreenerResponse(BaseModel):
    """
    Model for the response from the Screener tool.
    Matches the flattened structure returned by Screener.scrape().
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Quarterly Growth Metrics
    yoy_sales_growth: Optional[float] = Field(None, alias="YOY Sales Growth %")
    qoq_sales_growth: Optional[float] = Field(None, alias="QOQ Sales Growth %")
    yoy_profit_growth: Optional[float] = Field(None, alias="YOY Profit Growth %")
    qoq_profit_growth: Optional[float] = Field(None, alias="QOQ Profit Growth %")

    # Quarterly Results
    sales: Optional[float] = Field(None, alias="Sales")
    expenses: Optional[float] = Field(None, alias="Expenses")
    operating_profit: Optional[float] = Field(None, alias="Operating Profit")
    opm: Optional[float] = Field(None, alias="OPM")
    other_income: Optional[float] = Field(None, alias="Other Income")
    interest: Optional[float] = Field(None, alias="Interest")
    depreciation: Optional[float] = Field(None, alias="Depreciation")
    profit_before_tax: Optional[float] = Field(None, alias="Profit before tax")
    tax: Optional[float] = Field(None, alias="Tax")
    net_profit: Optional[float] = Field(None, alias="Net Profit")
    eps_in_rs: Optional[float] = Field(None, alias="EPS in Rs")
    raw_pdf: Optional[Any] = Field(None, alias="Raw PDF")

    # Annual Results
    annual_sales: Optional[float] = Field(None, alias="Annual Sales")
    annual_expenses: Optional[float] = Field(None, alias="Annual Expenses")
    annual_operating_profit: Optional[float] = Field(
        None, alias="Annual Operating Profit"
    )
    annual_opm: Optional[float] = Field(None, alias="Annual OPM")
    annual_other_income: Optional[float] = Field(None, alias="Annual Other Income")
    annual_interest: Optional[float] = Field(None, alias="Annual Interest")
    annual_depreciation: Optional[float] = Field(None, alias="Annual Depreciation")
    annual_profit_before_tax: Optional[float] = Field(
        None, alias="Annual Profit before tax"
    )
    annual_tax: Optional[float] = Field(None, alias="Annual Tax")
    annual_net_profit: Optional[float] = Field(None, alias="Annual Net Profit")
    annual_eps_in_rs: Optional[float] = Field(None, alias="Annual EPS in Rs")
    annual_dividend_payout: Optional[float] = Field(
        None, alias="Annual Dividend Payout"
    )

    # Balance Sheet
    balance_sheet_equity_capital: Optional[float] = Field(
        None, alias="Balance Sheet Equity Capital"
    )
    balance_sheet_reserves: Optional[float] = Field(
        None, alias="Balance Sheet Reserves"
    )
    balance_sheet_borrowings: Optional[float] = Field(
        None, alias="Balance Sheet Borrowings"
    )
    balance_sheet_other_liabilities: Optional[float] = Field(
        None, alias="Balance Sheet Other Liabilities"
    )
    balance_sheet_total_liabilities: Optional[float] = Field(
        None, alias="Balance Sheet Total Liabilities"
    )
    balance_sheet_fixed_assets: Optional[float] = Field(
        None, alias="Balance Sheet Fixed Assets"
    )
    balance_sheet_cwip: Optional[float] = Field(None, alias="Balance Sheet CWIP")
    balance_sheet_investments: Optional[float] = Field(
        None, alias="Balance Sheet Investments"
    )
    balance_sheet_other_assets: Optional[float] = Field(
        None, alias="Balance Sheet Other Assets"
    )
    balance_sheet_total_assets: Optional[float] = Field(
        None, alias="Balance Sheet Total Assets"
    )

    # Cash Flow
    cash_flow_operating_activity: Optional[float] = Field(
        None, alias="Cash Flow Cash from Operating Activity"
    )
    cash_flow_investing_activity: Optional[float] = Field(
        None, alias="Cash Flow Cash from Investing Activity"
    )
    cash_flow_financing_activity: Optional[float] = Field(
        None, alias="Cash Flow Cash from Financing Activity"
    )
    cash_flow_net_flow: Optional[float] = Field(None, alias="Cash Flow Net Cash Flow")
    cash_flow_free_flow: Optional[float] = Field(None, alias="Cash Flow Free Cash Flow")
    cash_flow_cfo_op: Optional[float] = Field(None, alias="Cash Flow CFO/OP")

    # Shareholding
    quarterly_holding_promoters: Optional[float] = Field(
        None, alias="Quarterly Shareholding Promoters"
    )
    quarterly_holding_fiis: Optional[float] = Field(
        None, alias="Quarterly Shareholding FIIs"
    )
    quarterly_holding_diis: Optional[float] = Field(
        None, alias="Quarterly Shareholding DIIs"
    )
    quarterly_holding_public: Optional[float] = Field(
        None, alias="Quarterly Shareholding Public"
    )
    quarterly_holding_no_shareholders: Optional[float] = Field(
        None, alias="Quarterly Shareholding No. of Shareholders"
    )

    annual_holding_promoters: Optional[float] = Field(
        None, alias="Annual Shareholding Promoters"
    )
    annual_holding_fiis: Optional[float] = Field(None, alias="Annual Shareholding FIIs")
    annual_holding_diis: Optional[float] = Field(None, alias="Annual Shareholding DIIs")
    annual_holding_public: Optional[float] = Field(
        None, alias="Annual Shareholding Public"
    )
    annual_holding_no_shareholders: Optional[float] = Field(
        None, alias="Annual Shareholding No. of Shareholders"
    )

    # Growth Metrics
    sales_growth_10yrs: Optional[float] = Field(
        None, alias="Compounded Sales Growth 10 Years"
    )
    sales_growth_5yrs: Optional[float] = Field(
        None, alias="Compounded Sales Growth 5 Years"
    )
    sales_growth_3yrs: Optional[float] = Field(
        None, alias="Compounded Sales Growth 3 Years"
    )
    sales_growth_ttm: Optional[float] = Field(None, alias="Compounded Sales Growth TTM")

    profit_growth_10yrs: Optional[float] = Field(
        None, alias="Compounded Profit Growth 10 Years"
    )
    profit_growth_5yrs: Optional[float] = Field(
        None, alias="Compounded Profit Growth 5 Years"
    )
    profit_growth_3yrs: Optional[float] = Field(
        None, alias="Compounded Profit Growth 3 Years"
    )
    profit_growth_ttm: Optional[float] = Field(
        None, alias="Compounded Profit Growth TTM"
    )

    stock_cagr_10yrs: Optional[float] = Field(None, alias="Stock Price CAGR 10 Years")
    stock_cagr_5yrs: Optional[float] = Field(None, alias="Stock Price CAGR 5 Years")
    stock_cagr_3yrs: Optional[float] = Field(None, alias="Stock Price CAGR 3 Years")
    stock_cagr_1yr: Optional[float] = Field(None, alias="Stock Price CAGR 1 Year")

    roe_10yrs: Optional[float] = Field(None, alias="Return on Equity 10 Years")
    roe_5yrs: Optional[float] = Field(None, alias="Return on Equity 5 Years")
    roe_3yrs: Optional[float] = Field(None, alias="Return on Equity 3 Years")
    roe_last_year: Optional[float] = Field(None, alias="Return on Equity Last Year")

    # Ratios
    market_cap: Optional[float] = Field(None, alias="Market Cap")
    current_price: Optional[float] = Field(None, alias="Current Price")
    high_low: Optional[float] = Field(None, alias="High / Low")
    stock_pe: Optional[float] = Field(None, alias="Stock P/E")
    book_value: Optional[float] = Field(None, alias="Book Value")
    dividend_yield: Optional[float] = Field(None, alias="Dividend Yield")
    roce: Optional[float] = Field(None, alias="ROCE")
    roe: Optional[float] = Field(None, alias="ROE")
    face_value: Optional[float] = Field(None, alias="Face Value")

    # Metadata
    about: Optional[str] = Field(None, alias="About")

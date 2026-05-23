from typing import List, Dict, Optional, Union, Any
from pydantic import BaseModel, Field, ConfigDict

# This file contains the Pydantic model for the Screener tool response.
# Other tool response schemas should be added as separate files in src/models/
# or added here if they are closely related.

class AnnualReport(BaseModel):
    year: str
    url: str

class CreditRating(BaseModel):
    organization: str
    date: str
    url: str

class FinancialMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    sales: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Sales")
    expenses: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Expenses")
    operating_profit: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Operating Profit")
    opm: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="OPM")
    other_income: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Other Income")
    interest: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Interest")
    depreciation: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Depreciation")
    profit_before_tax: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Profit before tax")
    tax: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Tax")
    net_profit: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Net Profit")
    eps_in_rs: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="EPS in Rs")
    dividend_payout: Optional[Dict[str, Optional[Union[str, float, int]]]] = Field(None, alias="Dividend Payout")
    raw_pdf: Optional[Dict[str, Any]] = Field(None, alias="Raw PDF")

class BalanceSheetMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    equity_capital: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Equity Capital")
    reserves: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Reserves")
    borrowings: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Borrowings")
    other_liabilities: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Other Liabilities")
    total_liabilities: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Total Liabilities")
    fixed_assets: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Fixed Assets")
    cwip: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="CWIP")
    investments: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Investments")
    other_assets: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Other Assets")
    total_assets: Optional[Dict[str, Optional[Union[float, int, str]]]] = Field(None, alias="Total Assets")

class CashFlowMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    operating_activity: Optional[Dict[str, Optional[str]]] = Field(None, alias="Cash from Operating Activity")
    investing_activity: Optional[Dict[str, Optional[str]]] = Field(None, alias="Cash from Investing Activity")
    financing_activity: Optional[Dict[str, Optional[str]]] = Field(None, alias="Cash from Financing Activity")
    net_cash_flow: Optional[Dict[str, Optional[str]]] = Field(None, alias="Net Cash Flow")
    free_cash_flow: Optional[Dict[str, Optional[str]]] = Field(None, alias="Free Cash Flow")

class ShareholdingMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    promoters: Optional[Dict[str, Optional[str]]] = Field(None, alias="Promoters")
    fiis: Optional[Dict[str, Optional[str]]] = Field(None, alias="FIIs")
    diis: Optional[Dict[str, Optional[str]]] = Field(None, alias="DIIs")
    public: Optional[Dict[str, Optional[str]]] = Field(None, alias="Public")
    no_of_shareholders: Optional[Dict[str, Optional[str]]] = Field(None, alias="No. of Shareholders")

class ScreenerResponse(BaseModel):
    """
    Model for the response from the Screener tool.
    Matches the structure returned by Screener.scrape().
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Metrics like Sales, Expenses, Net Profit, etc. for periods
    quarterly_results: Optional[FinancialMetrics] = Field(None, alias="quarterly-results")
    annual_results: Optional[FinancialMetrics] = Field(None, alias="annual-results")
    
    # Growth and historical comparisons typically in strings (percentages)
    sales_growth: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="sales-growth")
    profit_growth: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="profit-growth")
    price_cagr: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="price-cagr")
    return_on_equity: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="return-on-equity")
    
    # Financial statements
    balance_sheet: Optional[BalanceSheetMetrics] = Field(None, alias="balance-sheet")
    cash_flow: Optional[CashFlowMetrics] = Field(None, alias="cash-flow")
    
    # Top ratios (processed as floats by the scraper)
    ratios: Optional[Dict[str, Any]] = None
    
    # Shareholding data
    quarterly_shareholding: Optional[ShareholdingMetrics] = Field(None, alias="quarterly-shareholding")
    annual_shareholding: Optional[ShareholdingMetrics] = Field(None, alias="annual-shareholding")
    
    # Metadata
    about: Optional[str] = None
    annual_report: Optional[List[AnnualReport]] = Field(None, alias="annual-report")
    credit_ratings: Optional[List[CreditRating]] = Field(None, alias="credit-ratings")

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

class ScreenerResponse(BaseModel):
    """
    Model for the response from the Screener tool.
    Matches the structure returned by Screener.scrape().
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Metrics like Sales, Expenses, Net Profit, etc. for periods
    quarterly_results: Optional[Dict[str, Dict[str, Optional[Union[str, float, int]]]]] = Field(None, alias="quarterly-results")
    annual_results: Optional[Dict[str, Dict[str, Optional[Union[str, float, int]]]]] = Field(None, alias="annual-results")
    
    # Growth and historical comparisons typically in strings (percentages)
    sales_growth: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="sales-growth")
    profit_growth: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="profit-growth")
    price_cagr: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="price-cagr")
    return_on_equity: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="return-on-equity")
    
    # Financial statements
    balance_sheet: Optional[Dict[str, Dict[str, Optional[Union[float, int, str]]]]] = Field(None, alias="balance-sheet")
    cash_flow: Optional[Dict[str, Dict[str, Optional[Union[str, float, int]]]]] = Field(None, alias="cash-flow")
    
    # Top ratios (processed as floats by the scraper)
    ratios: Optional[Dict[str, Any]] = None
    
    # Shareholding data
    quarterly_shareholding: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="quarterly-shareholding")
    annual_shareholding: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(None, alias="annual-shareholding")
    
    # Metadata
    about: Optional[str] = None
    annual_report: Optional[List[AnnualReport]] = Field(None, alias="annual-report")
    credit_ratings: Optional[List[CreditRating]] = Field(None, alias="credit-ratings")

from pydantic import BaseModel, Field
from typing import Optional

class MarketSmithIndiaResponse(BaseModel):
    symbol: str
    master_score: Optional[int] = Field(None, alias="Master Score")
    eps_rating: Optional[int] = Field(None, alias="EPS Rating")
    price_strength: Optional[int] = Field(None, alias="Price Strength")
    acc_dis_rating: Optional[str] = Field(None, alias="Acc/Dis Rating")
    group_rank: Optional[str] = Field(None, alias="Group Rank")
    eps_growth_rate: Optional[str] = Field(None, alias="EPS Growth Rate")
    earnings_stability: Optional[int] = Field(None, alias="Earnings Stability")
    pe_ratio: Optional[float] = Field(None, alias="P/E Ratio")
    pe_5year_range: Optional[str] = Field(None, alias="5-Year P/E Range")
    return_on_equity: Optional[str] = Field(None, alias="Return on Equity")
    cash_flow: Optional[float] = Field(None, alias="Cash Flow (INR)")

    class Config:
        populate_by_name = True

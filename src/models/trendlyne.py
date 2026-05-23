from typing import List, Dict, Optional, Union, Any
from pydantic import BaseModel, Field, ConfigDict

class SwotScore(BaseModel):
    score: float

class SwotData(BaseModel):
    O: Optional[SwotScore] = None
    S: Optional[SwotScore] = None
    T: Optional[SwotScore] = None
    W: Optional[SwotScore] = None

class TechnicalInsight(BaseModel):
    longtext: Optional[str] = None
    shorttext: Optional[str] = None

class TechnicalIndicator(BaseModel):
    name: Optional[str] = None
    value: Optional[Union[float, int, bool, str]] = None
    color: Optional[str] = None
    description: Optional[str] = None
    insight: Optional[Union[TechnicalInsight, str, None]] = None

class BetaItem(BaseModel):
    label: str
    data: Optional[float] = None
    color: str

class PriceAnalysisItem(BaseModel):
    name: str
    change: Optional[float] = None
    changePercent: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    color: str

class PriceInsightItem(BaseModel):
    title: str
    shorttext: str
    longtext: str
    value: Optional[float] = None
    color: str

class MaSignal(BaseModel):
    bearish: int
    bullish: int
    ema_insight: Optional[str] = None
    sma_insight: Optional[str] = None
    ema_total: int
    sma_total: int

class TrendlyneParameters(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    NSEcode: Optional[str] = None
    BSEcode: Optional[str] = None
    current_price: Optional[float] = None
    
    # Moving Averages
    ema_parameters: Optional[List[TechnicalIndicator]] = None
    sma_parameters: Optional[List[TechnicalIndicator]] = None
    ma_signal: Optional[MaSignal] = None
    
    # Oscillators
    oscillator_parameter: Optional[List[TechnicalIndicator]] = None
    oscillator_signal: Optional[Dict[str, Any]] = None
    
    # Insights
    price_insight: Optional[List[PriceInsightItem]] = None
    beta_insight: Optional[List[Dict[str, Any]]] = None
    
    # Analysis Lists
    price_analysis: Optional[List[PriceAnalysisItem]] = None
    beta_analysis: Optional[List[BetaItem]] = None
    
    # Common Indicators (directly in parameters)
    rsi: Optional[TechnicalIndicator] = None
    macd: Optional[TechnicalIndicator] = None
    macdsignal: Optional[TechnicalIndicator] = None
    macdhistogram: Optional[TechnicalIndicator] = None
    mfi: Optional[TechnicalIndicator] = None
    adx: Optional[TechnicalIndicator] = None
    momentum: Optional[TechnicalIndicator] = None

class TrendlyneBody(BaseModel):
    parameters: Optional[TrendlyneParameters] = None

class TrendlyneTechnicals(BaseModel):
    body: Optional[TrendlyneBody] = None

class TrendlyneResponse(BaseModel):
    """
    Model for the response from the Trendlyne tool.
    """
    model_config = ConfigDict(extra="allow")

    swot: Optional[SwotData] = None
    technicals: Optional[TrendlyneTechnicals] = None

from typing import List, Dict, Optional, Union, Any
from pydantic import BaseModel, Field, ConfigDict

class TrendlyneResponse(BaseModel):
    """
    Model for the response from the Trendlyne tool.
    Matches the flattened structure returned by Trendlyne.fetch().
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # SWOT Scores
    swot_s_score: Optional[float] = Field(None, alias="SWOT S Score")
    swot_w_score: Optional[float] = Field(None, alias="SWOT W Score")
    swot_o_score: Optional[float] = Field(None, alias="SWOT O Score")
    swot_t_score: Optional[float] = Field(None, alias="SWOT T Score")
    
    # Core Technicals & Metadata
    current_price: Optional[float] = Field(None, alias="current_price")
    nse_code: Optional[Union[str, int]] = Field(None, alias="NSEcode")
    bse_code: Optional[Union[str, int]] = Field(None, alias="BSEcode")
    stock_id: Optional[int] = Field(None, alias="stock_id")
    full_name: Optional[str] = Field(None, alias="get_full_name")
    last_modified: Optional[str] = Field(None, alias="last_modified")
    momentum_score: Optional[float] = Field(None, alias="Trendlyne Momentum Score")
    
    # MA Signals
    ma_signal_bullish: Optional[int] = Field(None, alias="MA Signal bullish")
    ma_signal_bearish: Optional[int] = Field(None, alias="MA Signal bearish")
    ma_signal_ema_total: Optional[int] = Field(None, alias="MA Signal ema_total")
    ma_signal_sma_total: Optional[int] = Field(None, alias="MA Signal sma_total")
    ma_signal_sma_bullish: Optional[int] = Field(None, alias="MA Signal sma_bullish")
    ma_signal_sma_bearish: Optional[int] = Field(None, alias="MA Signal sma_bearish")
    ma_signal_ema_bullish: Optional[int] = Field(None, alias="MA Signal ema_bullish")
    ma_signal_ema_bearish: Optional[int] = Field(None, alias="MA Signal ema_bearish")
    ma_signal_sma_insight: Optional[str] = Field(None, alias="MA Signal sma_insight")
    ma_signal_ema_insight: Optional[str] = Field(None, alias="MA Signal ema_insight")
    ma_signal_insight: Optional[str] = Field(None, alias="MA Signal insight")
    ma_signal_color: Optional[str] = Field(None, alias="MA Signal color")

    # SMAs
    sma_5: Optional[float] = Field(None, alias="SMA 5 Day")
    sma_10: Optional[float] = Field(None, alias="SMA 10 Day")
    sma_20: Optional[float] = Field(None, alias="SMA 20 Day")
    sma_30: Optional[float] = Field(None, alias="SMA 30 Day")
    sma_50: Optional[float] = Field(None, alias="SMA 50 Day")
    sma_100: Optional[float] = Field(None, alias="SMA 100 Day")
    sma_150: Optional[float] = Field(None, alias="SMA 150 Day")
    sma_200: Optional[float] = Field(None, alias="SMA 200 Day")

    # EMAs
    ema_5: Optional[float] = Field(None, alias="EMA 5 Day")
    ema_10: Optional[float] = Field(None, alias="EMA 10 Day")
    ema_12: Optional[float] = Field(None, alias="EMA 12 Day")
    ema_20: Optional[float] = Field(None, alias="EMA 20 Day")
    ema_26: Optional[float] = Field(None, alias="EMA 26 Day")
    ema_50: Optional[float] = Field(None, alias="EMA 50 Day")
    ema_100: Optional[float] = Field(None, alias="EMA 100 Day")
    ema_200: Optional[float] = Field(None, alias="EMA 200 Day")

    # Indicators
    rsi: Optional[float] = Field(None, alias="RSI")
    macd: Optional[float] = Field(None, alias="MACD")
    mfi: Optional[float] = Field(None, alias="MFI")
    adx: Optional[float] = Field(None, alias="ADX")
    rsi_14: Optional[float] = Field(None, alias="RSI(14)")
    macd_12_26_9: Optional[float] = Field(None, alias="MACD(12, 26, 9)")
    macd_histogram: Optional[float] = Field(None, alias="MACD Histogram")
    macd_signal: Optional[float] = Field(None, alias="MACD Signal")
    roc_21: Optional[float] = Field(None, alias="ROC(21)")
    roc_125: Optional[float] = Field(None, alias="ROC(125)")
    atr: Optional[float] = Field(None, alias="ATR")
    adxr: Optional[float] = Field(None, alias="ADXR")
    william: Optional[float] = Field(None, alias="William")
    cci_20: Optional[float] = Field(None, alias="CCI 20")
    awesome_oscillator: Optional[float] = Field(None, alias="Awesome Oscillator")
    momentum_oscillator: Optional[float] = Field(None, alias="Momentum Oscillator")
    stochastic_oscillator: Optional[float] = Field(None, alias="Stochastic Oscillator")
    stochastic_rsi: Optional[float] = Field(None, alias="Stochastic RSI")
    ultimate_oscillator: Optional[float] = Field(None, alias="Ultimate Oscillator")

    # Price Performance
    price_1d_change: Optional[float] = Field(None, alias="PRICE 1 Day Change %")
    price_1w_change: Optional[float] = Field(None, alias="PRICE 1 Week Change %")
    price_1m_change: Optional[float] = Field(None, alias="PRICE 1 Month Change %")
    price_3m_change: Optional[float] = Field(None, alias="PRICE 3 Months Change %")
    price_6m_change: Optional[float] = Field(None, alias="PRICE 6 Months Change %")
    price_1y_change: Optional[float] = Field(None, alias="PRICE 1 Year Change %")
    price_3y_change: Optional[float] = Field(None, alias="PRICE 3 Year Change %")
    price_5y_change: Optional[float] = Field(None, alias="PRICE 5 Year Change %")
    price_52w_high: Optional[float] = Field(None, alias="PRICE 52 week high")
    price_52w_low: Optional[float] = Field(None, alias="PRICE 52 week low")

    # Beta
    beta_1m: Optional[float] = Field(None, alias="BETA 1 Month")
    beta_3m: Optional[float] = Field(None, alias="BETA 3 Month")
    beta_1y: Optional[float] = Field(None, alias="BETA 1 Year")
    beta_3y: Optional[float] = Field(None, alias="BETA 3 Year")
    beta_benchmark: Optional[str] = Field(None, alias="beta_benchmark_index")

    # Day Trading
    day_low: Optional[float] = Field(None, alias="day_low")
    day_high: Optional[float] = Field(None, alias="day_high")
    day_change: Optional[float] = Field(None, alias="day_change")
    day_change_percent: Optional[float] = Field(None, alias="day_changeP")



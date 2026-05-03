from src.utils.ratios import (
        pat_margin,
        ebitda_margin,
        ebitda_growth,
        pat_growth,
        return_on_equity,
        return_on_assets,
        interest_coverage,
        financial_leverage,
        total_assets_turnover,
        price_to_earnings,
        price_to_sales,
    )


from src.utils.ratios import (
        fetch_raw_results,
        calc_fundamental_ratios, 
        calc_valuations_time_series,
        process_raw_results
    )
from src.utils.helpers import get_date_shifted, get_curr_date_str


import requests
import pandas as pd
import yfinance as yf
from typing import Optional, List, Dict, Union, Any

from src.utils import console, track


def fetch_n_insert_symbols_n_exchange_to_db():
    collection_name = "symbols"

    

    pass

def fetch_n_insert_symbol_financials_to_db():
    pass

def fetch_n_insert_exchange_financials_to_db():
    pass

def generate_yf_symbol(symbol:str, exchange:str):
    yahoo_suffix_map = {
        "NSE": ".NS", "BSE": ".BO", "ASX": ".AX", "VIENNA": ".VI", "BRUSSELS": ".BR",
        "BOVESPA": ".SA", "TO": ".TO", "TSXV": ".V", "SN": ".SN", "SS": ".SS",
        "SZ": ".SZ", "CO": ".CO", "PRAGUE": ".PR", "PARIS": ".PA", "HKEX": ".HK",
        "TWSE": ".TW", "TWO": ".TWO", "LSE": ".L", "XETRA": ".DE", "KSE": ".KS",
        "KOSDAQ": ".KQ", "JAKARTA": ".JK", "SINGAPORE": ".SI", "MEXICO": ".MX",
        "MILAN": ".MI", "MOSCOW": ".ME", "STOCKHOLM": ".ST", "SWISS": ".SW",
        "TAIWAN_OTC": ".TWO", "TAIWAN": ".TW", "ATHENS": ".AT", "OSLO": ".OL",
        "LISBON": ".LS", "SAUDI": ".SAU",
        # US exchanges often have no suffix
        "NASDAQ": "", "NYSE": "", "AMEX": ""
    }

    if exchange not in yahoo_suffix_map:
        raise ValueError(f"Unknown exchange: {exchange}")

    return symbol + yahoo_suffix_map[exchange]


def fetch_yf_symbols_prices(
    symbols_map: List[Dict],
    from_date: str = None,
    to_date: str = None
) -> pd.DataFrame:
    """
    Args:
        symbols_map (list of dict): [{"symbol": "AAPL", "exchange": "NASDAQ"}, ...]
    """

    symlist = []
    for sym in symbols_map:
        exchange = sym['exchange'].upper().strip()
        symbol = sym['symbol']

        symlist.append(generate_yf_symbol(symbol, exchange))

    # fetch prices
    prices_df = yf.download(symlist, start=from_date, end=to_date, progress=False, auto_adjust=True)
    return prices_df


def get_symbol_prices(prices_df: pd.DataFrame, symbol: str) -> pd.Series:
  
    # Check if DataFrame has MultiIndex (multiple tickers) or just one
    if isinstance(prices_df.columns, pd.MultiIndex):
        if "Adj Close" in prices_df.columns.levels[0]:
            if symbol in prices_df["Adj Close"]:
                return prices_df["Adj Close"][symbol]
        # fallback
        return prices_df["Close"][symbol]
    else:
        # Single ticker case (flat columns)
        if "Adj Close" in prices_df.columns:
            return prices_df["Adj Close"]
        return prices_df["Close"]

def fundamentals_pipeline(
        symbols: List[str],
        period = "quarterly",
        consolidated=True,
        from_date = get_date_shifted(get_curr_date_str(), -30*12*7) ,
        to_date = get_curr_date_str(),
    ):

    result = {}
    for symbol in symbols:

        df = fetch_raw_results(symbol=symbol, period=period, consolidated=consolidated, from_date=from_date, to_date=to_date)

        processed_results_df = process_raw_results(df.copy(), period=period)

        fundamentals_df = calc_fundamental_ratios(processed_results_df.copy(), period=period)

        fundamentals_df['symbol'] = symbol
        fundamentals_df['period'] = period
        fundamentals_df['exchange'] = 'NSE'

        result[symbol] = fundamentals_df.to_dict(orient='records')

    return result

def valuations_pipeline(
        symbols: List[str],
        exchange : str = "NSE",
        period = "quarterly", # try not to touch
        consolidated=True,
        from_date = get_date_shifted(get_curr_date_str(), -30*12*7) ,
        to_date = get_curr_date_str(),
    ):
    result = {}
    symbols_map = [ {"symbol" : symbol, "exchange" : exchange} for symbol in symbols ]
    prices_df = fetch_yf_symbols_prices(symbols_map, from_date, to_date)

    for symbol in symbols:
        df_q = fetch_raw_results(symbol=symbol, period=period, consolidated=consolidated, from_date=from_date, to_date=to_date)
        processed_results_df_q = process_raw_results(df_q.copy(), period=period)


        symbol_prices_df = get_symbol_prices(prices_df, generate_yf_symbol(symbol, exchange))
        symbol_prices_df = symbol_prices_df.rename('price')
        symbol_prices_df = symbol_prices_df.rename_axis('date')

        valuation_df = calc_valuations_time_series(processed_results_df_q, symbol_prices_df)
        valuation_df['symbol'] = symbol
        valuation_df['period'] = period
        valuation_df['exchange'] = exchange

        result[symbol] = valuation_df.to_dict(orient = 'records')

    return result

def ratios_pipeline(
        symbols: List[str],
        exchange : str = "NSE",
        period: str = "quarterly",
        from_date = get_date_shifted(get_curr_date_str(), -30*12*7) ,
        to_date = get_curr_date_str(),
        consolidated : bool = True
    ):

    # FETCHING ALL YF PRICES AT ONCE

    symbols_map = [ {"symbol" : symbol, "exchange" : exchange} for symbol in symbols ]
    prices_df = fetch_yf_symbols_prices(symbols_map, from_date, to_date)

    for symbol in track(symbols, total = len(symbols), description="Calculating ratios..."):
        
        # FETCHING RAW DATA
        df_q = fetch_raw_results(symbol=symbol, period="quarterly", consolidated=consolidated, from_date=from_date, to_date=to_date)
        if period=="quarter":
            df = df_q
        else:
            df_a = fetch_raw_results(symbol=symbol, period=period, consolidated=consolidated, from_date=from_date, to_date=to_date)
            df = df_a

        processed_results_df_q = process_raw_results(df_q.copy(), period="quarterly")
        if period=="quarter":
            processed_results_df = processed_results_df_q
        else:
            processed_results_df_a = process_raw_results(df_a.copy(), period=period)
            processed_results_df = processed_results_df_a

        symbol_prices_df = get_symbol_prices(prices_df, generate_yf_symbol(symbol, exchange))
        symbol_prices_df = symbol_prices_df.rename('price')
        symbol_prices_df = symbol_prices_df.rename_axis('date')
        
        # CALCULATING RATIOS
        
        # ==============================================
        # ======== Part 1  =====================
        fundamentals_df = calc_fundamental_ratios(processed_results_df.copy(), period=period)
        # ==============================================

        # VALUATIONS - TIME SERIES

        # ==============================================
        # ======== Part 2 complete =====================
        valuation_df = calc_valuations_time_series(processed_results_df_q, symbol_prices_df)
        # ==============================================

    
    return {
        "fundamentals" : fundamentals_df.to_dict(orient = 'records'),
        "valuations" : valuation_df.to_dict(orient = 'records'),
    }


def calculate_exchange_ratios_n_insert_to_db():
    # fetch a single df with all cols
    pass



# blog
import os
from datetime import datetime

import pandas as pd
import yfinance as yf

from .utils import load_json, write_json


class Technicals(object):
    """docstring for Technicals."""

    def __init__(self, nse_dir):
        super(Technicals, self).__init__()
        self.nse_dir = nse_dir
        os.makedirs(self.nse_dir, exist_ok=True)

        self.period = "1y"

    def technicals(self, symbol, force=False):
        share_dir = os.path.join(self.nse_dir, symbol)
        technicals_dir = os.path.join(share_dir, "technicals")
        technicals_file = os.path.join(technicals_dir, "technicals.csv")
        metadata_path = os.path.join(share_dir, "metadata.json")
        ticker = f"{symbol}.NS"

        # metadata obvio
        metadata = load_json(metadata_path)

        technicals_df = pd.DataFrame()
        if os.path.exists(technicals_file):
            print("Reading Technicals")
            technicals_df = pd.read_csv(technicals_file, index_col=0)

        if not os.path.exists(technicals_file) or force:
            latest_df = yf.download(ticker, period=self.period)
            # Example 1: Simple Moving Average (SMA)
            latest_df["SMA_20"] = talib.SMA(latest_df["Close"], timeperiod=20)
            latest_df["SMA_50"] = talib.SMA(latest_df["Close"], timeperiod=50)

            # Example 2: Exponential Moving Average (EMA)
            latest_df["EMA_12"] = talib.EMA(latest_df["Close"], timeperiod=12)
            latest_df["EMA_26"] = talib.EMA(latest_df["Close"], timeperiod=26)

            # Example 3: Relative Strength Index (RSI)
            latest_df["RSI"] = talib.RSI(latest_df["Close"], timeperiod=14)

            # Example 4: Moving Average Convergence Divergence (MACD)
            macd, macdsignal, macdhist = talib.MACD(
                latest_df["Close"], fastperiod=12, slowperiod=26, signalperiod=9
            )
            latest_df["MACD"] = macd
            latest_df["MACDSignal"] = macdsignal
            latest_df["MACDHist"] = macdhist

            # Example 5: Bollinger Bands
            upperband, middleband, lowerband = talib.BBANDS(
                latest_df["Close"], timeperiod=20, nbdevup=2, nbdevdn=2
            )
            latest_df["BB_Upper"] = upperband
            latest_df["BB_Middle"] = middleband
            latest_df["BB_Lower"] = lowerband

            # merge latest data with existing
            combined_df = pd.concat([technicals_df, latest_df])
            combined_df = combined_df[~combined_df.index.duplicated(keep="last")]
            combined_df.sort_index(inplace=True)
            technicals_df = combined_df
            technicals_df.to_csv(technicals_file)

            # update metadata
            metadata["data"]["technicals"]["last_update"] = str(datetime.today())

        last_update = metadata["data"]["technicals"]["last_update"]
        write_json(metadata, metadata_path)

        return technicals_df, last_update

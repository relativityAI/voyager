import asyncio

import yfinance as yf
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.engine import get_session_factory
from src.db.models import (
    PriceOHLCV,
)

_EXCHANGE_TO_YF_SUFFIX = {
    "NSE": ".NS",
    "BSE": ".BO",
    "NYSE": "",
    "NASDAQ": "",
}

_COUNTRY_TO_EXCHANGES = {
    "IN": ["NSE", "BSE"],
    "US": ["NYSE", "NASDAQ"],
}


def _yf_symbol(symbol: str, exchange: str) -> str:
    suffix = _EXCHANGE_TO_YF_SUFFIX.get(exchange, "")
    return f"{symbol}{suffix}"


async def download_ohlcv(
    symbol: str,
    exchange: str,
    country: str,
    period: str = "max",
    interval: str = "1d",
    force: bool = False,
    incremental: bool = True,
) -> int:
    symbol = symbol.upper()
    exchange = exchange.upper()
    country = country.upper()

    session_factory = get_session_factory()
    async with session_factory() as session:
        from sqlalchemy import func as sql_func
        existing = await session.scalar(
            select(sql_func.count(PriceOHLCV.id)).where(
                PriceOHLCV.symbol == symbol,
                PriceOHLCV.exchange == exchange,
            )
        )
        if existing and existing > 0 and not force and not incremental:
            logger.info(f"[PRICE] {symbol}.{exchange} already has {existing} rows — skipping")
            return -1

    start_date = end_date = None
    if incremental and existing and existing > 0 and not force:
        async with session_factory() as session:
            max_date = await session.scalar(
                select(sql_func.max(PriceOHLCV.date)).where(
                    PriceOHLCV.symbol == symbol,
                    PriceOHLCV.exchange == exchange,
                )
            )
        if max_date:
            from datetime import date as date_type
            from datetime import timedelta
            today = date_type.today()
            start_date = max_date + timedelta(days=1)
            if start_date > today:
                logger.info(f"[PRICE] {symbol}.{exchange} already up to date ({max_date})")
                return 0
            end_date = today
            logger.info(f"[PRICE] {symbol}.{exchange}: fetching {start_date} → {end_date}")

    yf_sym = _yf_symbol(symbol, exchange)

    def _fetch():
        ticker = yf.Ticker(yf_sym)
        if start_date and end_date:
            return ticker.history(start=start_date, end=end_date, interval=interval, auto_adjust=False)
        return ticker.history(period=period, interval=interval, auto_adjust=False)

    try:
        hist = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=30)
    except asyncio.TimeoutError:
        logger.warning(f"[PRICE] Timeout fetching {yf_sym}")
        return 0

    if hist is None or hist.empty:
        logger.warning(f"[PRICE] No data for {yf_sym} ({exchange})")
        return 0

    rows = []
    for ts, row in hist.iterrows():
        rows.append({
            "symbol": symbol,
            "exchange": exchange,
            "country": country,
            "date": ts.date(),
            "open": float(row["Open"]) if row["Open"] == row["Open"] else None,
            "high": float(row["High"]) if row["High"] == row["High"] else None,
            "low": float(row["Low"]) if row["Low"] == row["Low"] else None,
            "close": float(row["Close"]) if row["Close"] == row["Close"] else None,
            "adjusted_close": (
                float(row["Adj Close"])
                if "Adj Close" in row and row["Adj Close"] == row["Adj Close"]
                else None
            ),
            "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
        })

    written = await _upsert_rows(rows)
    if written > 0:
        await _update_ledger(symbol, exchange, country)
    return written


async def _upsert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    session_factory = get_session_factory()
    batch_size = 200
    total = 0
    async with session_factory() as session:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            stmt = pg_insert(PriceOHLCV).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "exchange", "date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "adjusted_close": stmt.excluded.adjusted_close,
                    "volume": stmt.excluded.volume,
                },
            )
            await session.execute(stmt)
            total += len(batch)
        await session.commit()
    logger.info(f"[PRICE] Upserted {total} rows for {rows[0]['symbol']}.{rows[0]['exchange']}")
    return total


async def _update_ledger(symbol: str, exchange: str, country: str) -> None:
    from src.services._common import record_pull

    await record_pull(symbol, exchange, country)

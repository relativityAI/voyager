"""Price ingestion: download OHLCV bars and store them (CLI/cron path)."""


async def download_and_store(
    symbol: str,
    exchange: str,
    country: str,
    period: str = "max",
    interval: str = "1d",
) -> dict:
    from src.tools.prices import download_ohlcv

    rows = await download_ohlcv(symbol, exchange, country, period, interval, incremental=True)

    start_date = end_date = None
    if rows > 0:
        from sqlalchemy import select, func

        from src.db.engine import get_session_factory
        from src.db.models import PriceOHLCV

        async with get_session_factory()() as session:
            stmt = (
                select(func.min(PriceOHLCV.date), func.max(PriceOHLCV.date))
                .where(
                    PriceOHLCV.symbol == symbol.upper(),
                    PriceOHLCV.exchange == exchange.upper(),
                )
            )
            row = await session.execute(stmt)
            start_date, end_date = row.one()

    return {
        "symbol": symbol.upper(),
        "exchange": exchange.upper(),
        "country": country.upper(),
        "rows": rows,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }

from sqlalchemy import text

from . import engine as _engine

__all__ = ["ping_database", "init_db"]


async def ping_database() -> bool:
    # Read the live module attribute: init_db() rebinds engine/async_session
    # after this module is imported, so importing the names by value would pin
    # them to the pre-init None and report the DB as down forever.
    if _engine.engine is None:
        return False
    try:
        async with _engine.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def init_db():
    await _engine.init_db()

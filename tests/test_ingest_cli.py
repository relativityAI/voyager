"""Interface checks for the interactive ingest menu.

Only exercises the menu loop + dispatch wiring; ingest functions themselves
are covered elsewhere. Skips without a DB since ingest_mode calls init_db.
"""

import os
from unittest.mock import patch

import pytest

from src.db import engine  # noqa: F401  — importing loads .env (DATABASE_URL)

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")


def _scripted(answers):
    it = iter(answers)
    return lambda *a, **k: next(it)


def test_back_returns_cleanly():
    import ingest_cli

    with patch.object(ingest_cli.console, "input", _scripted(["b"])):
        ingest_cli.ingest_mode()


def test_invalid_choice_reprompts():
    import ingest_cli

    answers = iter(["x", "b"])
    with patch.object(ingest_cli.console, "input", lambda *a, **k: next(answers)):
        ingest_cli.ingest_mode()


def test_keyboard_interrupt_exits_cleanly():
    import ingest_cli

    def boom(*a, **k):
        raise KeyboardInterrupt

    with patch.object(ingest_cli.console, "input", boom):
        ingest_cli.ingest_mode()  # returns instead of propagating


def test_batch_routes_by_country_and_survives_failures(tmp_path, monkeypatch):
    import asyncio

    import cli_ui
    import ingest_cli

    calls = []

    async def nse(symbol, filing_type, refresh):
        if symbol == "VBL":
            raise RuntimeError("boom")
        calls.append(("NSE", symbol))
        return {"symbol": symbol, "saved": 1}

    async def sec(symbol, filing_type, refresh):
        calls.append(("SEC", symbol))
        return {"symbol": symbol, "saved": 1}

    monkeypatch.setattr(ingest_cli, "pull_nse_data", nse)
    monkeypatch.setattr(ingest_cli, "pull_sec_data", sec)
    monkeypatch.setattr(cli_ui, "INDICES_DIR", tmp_path)
    (tmp_path / "idx.txt").write_text("VBL NSE IN\nAAPL NASDAQ US\n")

    # scope=batch → index file → filing type=quarterly → refresh=no
    answers = iter(["2", "1", "1", "1"])
    monkeypatch.setattr(cli_ui.console, "input", lambda *a, **k: next(answers))
    asyncio.run(ingest_cli._financials())

    assert ("SEC", "AAPL") in calls  # country column routed US -> SEC, and the
    assert ("NSE", "VBL") not in calls  # VBL failure did not abort the run


def test_fresh_symbol_skips_without_fetching(monkeypatch):
    import asyncio

    import src.ingest.nse as nse

    async def always_fresh(symbol, filing_type):
        return True

    fetched = []

    def fake_fetch(url, symbol):
        fetched.append(url)

    monkeypatch.setattr(nse, "_statements_fresh", always_fresh)
    monkeypatch.setattr(nse, "_fetch_endpoint_json", fake_fetch)

    result = asyncio.run(nse.pull_nse_data("VBL", "quarterly", refresh=False))

    assert result == {"symbol": "VBL", "saved": 0, "skipped": "fresh"}
    assert fetched == []  # never hit the network


def test_announcement_dedup_index_is_functional():
    """raw_data JSONB can exceed the btree limit, so dedup must hash it."""
    import asyncio

    from sqlalchemy import text

    import src.db.engine as eng

    async def main():
        await eng.init_db()
        async with eng.engine.begin() as conn:
            row = (
                await conn.execute(
                    text("select indexdef from pg_indexes where indexname='uq_filing_raw'")
                )
            ).first()
        return row[0] if row else ""

    indexdef = asyncio.run(main())
    assert "md5" in indexdef.lower() and "raw_data" in indexdef.lower()

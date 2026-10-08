"""report_period_gte/lte/limit shipped with 290e281 untested — the missing
return it also shipped broke /financial-metrics in production (covered in
test_api_ratios). This covers the other two filters and the endpoint
forwarding."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.nse import _financial_history, get_statement_data


def _doc(period_end_date, **extra):
    m = MagicMock()
    m.to_dict.return_value = {
        "period_end_date": period_end_date,
        "consolidated": True,
        "symbol": "T",
        **extra,
    }
    return m


def _factory_with_rows(rows):
    res = MagicMock()
    res.scalars.return_value = MagicMock(all=MagicMock(return_value=rows))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=res)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), session


def _periods(resp):
    return [h["period_end_date"] for h in resp]


class TestFinancialHistoryRange:
    def _run(self, gte=None, lte=None):
        rows = [
            _doc("2024-03-31"),
            _doc("2024-06-30"),
            _doc("2024-09-30"),
        ]
        factory, _ = _factory_with_rows(rows)
        with patch("src.services.nse.get_session_factory", return_value=factory):
            return asyncio.run(
                _financial_history(
                    "T", "nse", "quarterly",
                    report_period_gte=gte, report_period_lte=lte,
                )
            )

    def test_no_bounds_returns_everything(self):
        assert len(self._run()) == 3

    def test_gte_filters_older_periods(self):
        assert _periods(self._run(gte="2024-06-01")) == ["2024-09-30", "2024-06-30"]

    def test_lte_filters_newer_periods(self):
        assert _periods(self._run(lte="2024-06-30")) == ["2024-06-30", "2024-03-31"]

    def test_both_bounds_are_inclusive(self):
        assert _periods(self._run(gte="2024-06-30", lte="2024-06-30")) == [
            "2024-06-30"
        ]

    def test_unparsable_bound_is_ignored_not_crash(self):
        assert len(self._run(gte="banana")) == 3


class TestStatementRange:
    def _run(self, gte=None, lte=None):
        factory, session = _factory_with_rows([_doc("2024-06-30")])
        count = MagicMock()
        count.scalar.return_value = 1
        session.execute = AsyncMock(side_effect=[count, MagicMock()])
        with patch("src.services.nse.get_session_factory", return_value=factory):
            resp = asyncio.run(
                get_statement_data(
                    "income-statements",
                    "T",
                    source="nse",
                    filing_type="quarterly",
                    report_period_gte=gte,
                    report_period_lte=lte,
                )
            )
        row_stmt = session.execute.await_args_list[1].args[0]
        return resp, row_stmt

    def _dates_in(self, stmt):
        return {
            v.strftime("%Y-%m-%d")
            for v in stmt.compile().params.values()
            if isinstance(v, datetime)
        }

    def test_gte_lte_land_in_the_query(self):
        resp, stmt = self._run(gte="2024-01-01", lte="2024-06-30")
        assert resp["pagination"]["total"] == 1
        assert {"2024-01-01", "2024-06-30"} <= self._dates_in(stmt)

    def test_no_bounds_no_date_params(self):
        _, stmt = self._run()
        assert not any(
            isinstance(v, datetime) for v in stmt.compile().params.values()
        )


class TestEndpointForwarding:
    def test_financials_forwards_range(self):
        from fastapi.testclient import TestClient

        from api import app

        client = TestClient(app)
        with patch(
            "api.get_financials", new=AsyncMock(return_value={})
        ) as m:
            resp = client.get(
                "/financials?symbol=T&source=nse&history=true"
                "&report_period_gte=2024-01-01&report_period_lte=2024-06-30"
            )
        assert resp.status_code == 200
        assert m.await_args.kwargs["report_period_gte"] == "2024-01-01"
        assert m.await_args.kwargs["report_period_lte"] == "2024-06-30"

    def test_statement_endpoint_forwards_range(self):
        from fastapi.testclient import TestClient

        from api import app

        client = TestClient(app)
        with patch(
            "api.get_statement_data", new=AsyncMock(return_value={})
        ) as m:
            resp = client.get(
                "/financials/income-statements?symbol=T&source=nse"
                "&report_period_gte=2024-01-01"
            )
        assert resp.status_code == 200
        assert m.await_args.kwargs["report_period_gte"] == "2024-01-01"

"""Verifies the SEC pull fixes:
- pull jobs that produce zero records are failed, not silently marked done
- empty SEC pulls do not fabricate metadata/"last_pull"
"""

import pytest

from src.jobs import _pull_outcome


def test_completed_pull_is_done():
    assert _pull_outcome({"status": "completed", "rows_written": 24}) == "done"


def test_partial_pull_with_rows_is_done():
    assert _pull_outcome({"status": "partial", "rows_written": 5}) == "done"


def test_partial_pull_with_zero_rows_is_failed():
    assert _pull_outcome({"status": "partial", "rows_written": 0}) == "failed"
    assert _pull_outcome({"status": "partial", "xbrl_parsed": 0}) == "failed"


def test_failed_pull_is_failed():
    assert _pull_outcome({"status": "failed", "rows_written": 0}) == "failed"


def test_empty_nse_pull_is_failed():
    # NSE statuses report xbrl_parsed, not rows_written
    assert _pull_outcome({"status": "partial", "xbrl_parsed": 0}) == "failed"


def test_no_data_with_existing_data_stays_done():
    # "no data" only occurs when nothing new was parseable (e.g. re-pull with
    # data already present); not treated as a failure.
    assert _pull_outcome({"status": "no data", "rows_written": 0}) == "done"


@pytest.mark.asyncio
async def test_parse_error_detail_populated_on_failure(monkeypatch):
    """The underlying SEC exception must reach parse_error_detail (regression:
    the original merge kept the plumbing but dropped the loop that fills it)."""
    from src.services import sec

    class _FakeCompany:
        cik = "123"

        def get_exchanges(self):
            raise Exception("no exchange")

    class _FakeResult:
        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            return _FakeResult()

        async def commit(self):
            pass

    def _raise(_company, _form, _n):
        raise RuntimeError("boom")

    monkeypatch.setattr(sec, "_one_company", lambda s: _FakeCompany())
    monkeypatch.setattr(sec, "_get_filings", _raise)
    monkeypatch.setattr(sec, "get_session_factory", lambda: lambda: _FakeSession())

    res = await sec.pull_sec_data("ZZZZ")
    assert res["status"] == "partial"
    assert res["rows_written"] == 0
    assert "RuntimeError: boom" in res["parse_error_detail"]
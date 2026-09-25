"""Job lifecycle: a hang must not hold a concurrency slot, and orphaned rows
must be swept without needing a restart."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.jobs as jobs
from src.db.models import PullJob
from src.utils.helpers import utcnow


class _SessionCM:
    """Stands in for the async_sessionmaker: called, then entered."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, one=None, all_of=None):
        self._one = one
        self._all = all_of or []

    def scalar_one(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._all


def _session(one=None, all_of=None):
    s = MagicMock()
    s.execute = AsyncMock(return_value=_Result(one, all_of))
    s.commit = AsyncMock()
    return s


def _job(**kw):
    return PullJob(id=1, job_id="j1", symbol="IBM", source="NSE", **kw)


@pytest.mark.asyncio
async def test_hung_pull_times_out_and_releases_the_slot(monkeypatch):
    monkeypatch.setattr(jobs, "JOB_TIMEOUT_SECONDS", 0.05)
    job = _job(status="queued")
    monkeypatch.setattr(jobs, "get_session_factory", lambda: _SessionCM(_session(one=job)))

    async def _hang(_job):
        await asyncio.sleep(30)

    monkeypatch.setattr(jobs, "_dispatch", _hang)
    await asyncio.wait_for(jobs._run_job(job), timeout=5)

    assert job.status == "failed"
    assert "Timed out" in job.error
    assert job.finished_at is not None
    assert job.job_id not in jobs._inflight


@pytest.mark.asyncio
async def test_reap_clears_orphaned_row(monkeypatch):
    stale = _job(status="running", started_at=utcnow())
    monkeypatch.setattr(
        jobs, "get_session_factory", lambda: _SessionCM(_session(all_of=[stale]))
    )

    await jobs.reap_stale_jobs()

    assert stale.status == "failed"
    assert "stale window" in stale.error
    assert stale.finished_at is not None


@pytest.mark.asyncio
async def test_reap_forever_sweeps_on_a_timer(monkeypatch):
    monkeypatch.setattr(jobs, "REAP_INTERVAL_SECONDS", 0.01)
    sweeps = []

    async def _reap():
        sweeps.append(1)

    monkeypatch.setattr(jobs, "reap_stale_jobs", _reap)
    reaper = asyncio.create_task(jobs.reap_forever())
    await asyncio.sleep(0.05)
    reaper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reaper

    assert len(sweeps) >= 2

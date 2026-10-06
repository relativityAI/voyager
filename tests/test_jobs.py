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

    def scalar_one_or_none(self):
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
async def test_cancel_frees_a_stuck_job(monkeypatch):
    """Audit P0-1: a stuck running job must be cancellable via the API."""
    stuck = _job(status="running", created_by="vgr_test0000")
    monkeypatch.setattr(jobs, "get_session_factory", lambda: _SessionCM(_session(one=stuck)))

    job = await jobs.cancel_job("j1", created_by="vgr_test0000")

    assert job.status == "failed"
    assert "Cancelled" in job.error
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_rejects_finished_job(monkeypatch):
    done = _job(status="done", created_by="vgr_test0000")
    monkeypatch.setattr(jobs, "get_session_factory", lambda: _SessionCM(_session(one=done)))

    with pytest.raises(jobs.JobNotCancellable):
        await jobs.cancel_job("j1", created_by="vgr_test0000")


@pytest.mark.asyncio
async def test_cancel_rejects_other_keys_job(monkeypatch):
    other = _job(status="running", created_by="vgr_other0000")
    monkeypatch.setattr(jobs, "get_session_factory", lambda: _SessionCM(_session(one=other)))

    with pytest.raises(PermissionError):
        await jobs.cancel_job("j1", created_by="vgr_test0000")


@pytest.mark.asyncio
async def test_queue_full_blocks_same_task_only(monkeypatch):
    """A full queue must reject only that task kind, not every other one."""

    class _CountResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    s = _session()

    async def _execute(stmt):
        params = getattr(stmt.compile(), "params", {})
        if "documents.parse" in str(params.values()):
            return _CountResult([("queued", 20)])  # queue full
        return _CountResult([("queued", 0)])  # plenty of room

    s.execute = _execute
    monkeypatch.setattr(jobs, "get_session_factory", lambda: _SessionCM(s))
    monkeypatch.setattr(jobs, "MAX_QUEUED_JOBS", 20)

    with pytest.raises(jobs.PullLimitReached):
        await jobs._check_queue_capacity(task="documents.parse")

    await jobs._check_queue_capacity(task="other.task")


@pytest.mark.asyncio
async def test_try_start_enforces_slots_and_single_start(monkeypatch):
    """A queued job starts once, and never past the concurrency cap."""
    started = []

    async def _fake_run(job):
        started.append(job.job_id)
        jobs._inflight.discard(job.job_id)
        jobs._inflight_kinds.pop(job.job_id, None)

    monkeypatch.setattr(jobs, "_run_job", _fake_run)
    monkeypatch.setattr(jobs, "MAX_CONCURRENT_PULLS", 1)

    a = PullJob(id=1, job_id="ja", symbol="A", source="NSE", status="queued")
    b = PullJob(id=2, job_id="jb", symbol="B", source="NSE", status="queued")
    try:
        assert jobs._try_start(a) is True
        assert jobs._try_start(a) is False  # already in flight — no double start
        assert jobs._try_start(b) is False  # slot full
        await asyncio.sleep(0)  # a's task runs and finishes
        assert jobs._try_start(b) is True  # slot free again
        await asyncio.sleep(0)
        assert started == ["ja", "jb"]
    finally:
        jobs._inflight.clear()
        jobs._inflight_kinds.clear()


@pytest.mark.asyncio
async def test_drain_promotes_queued_jobs(monkeypatch):
    """The drain loop starts queued jobs so nothing waits forever."""
    q1 = PullJob(id=1, job_id="jd1", symbol="A", source="NSE", status="queued")
    q2 = PullJob(id=2, job_id="jd2", symbol="B", source="NSE", status="queued")
    monkeypatch.setattr(
        jobs, "get_session_factory", lambda: _SessionCM(_session(all_of=[q1, q2]))
    )
    started = []
    monkeypatch.setattr(
        jobs, "_try_start", lambda job: started.append(job.job_id) or True
    )

    await jobs._drain_once()

    assert started == ["jd1", "jd2"]


@pytest.mark.asyncio
async def test_requeue_orphans_at_boot(monkeypatch):
    """A 'running' row from a dead process must return to the queue."""
    orphan = _job(status="running", started_at=utcnow())
    monkeypatch.setattr(
        jobs, "get_session_factory", lambda: _SessionCM(_session(all_of=[orphan]))
    )

    await jobs.requeue_orphaned_running()

    assert orphan.status == "queued"
    assert orphan.started_at is None


def test_post_callback_sends_json():
    """The webhook helper POSTs the completion payload."""
    from src.jobs import _post_callback
    import json as _json
    import threading
    import http.server

    received = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received.append(_json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        srv.timeout = 5  # don't hang forever on handle_request
        _post_callback(f"http://127.0.0.1:{port}/hook", {"event": "job.completed", "job_id": "j1", "status": "done"})
        srv.handle_request()  # process the incoming POST
    finally:
        srv.shutdown()

    assert received == [{"event": "job.completed", "job_id": "j1", "status": "done"}]


def test_post_callback_failure_is_swallowed():
    """A webhook failure must never propagate into the job runner."""
    from src.jobs import _post_callback

    # Port 1 is unroutable -> connection error -> swallowed.
    _post_callback("http://127.0.0.1:1/hook", {"event": "job.completed", "job_id": "j1"})


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

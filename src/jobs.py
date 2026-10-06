"""Async pull + analytics jobs.

POST /pull is long-running (NSE XBRL fetch + parse can take 30-120s+) and
Render hard-timeouts web requests at ~60s. Instead of blocking, the endpoint
submits a job and returns 202 with a job_id; clients poll GET /pull/jobs/{id}.

Jobs run as in-process asyncio tasks, so a worker restart orphans them. Two
guards keep a job from holding a concurrency slot forever:

* `_run_job` bounds every job with `JOB_TIMEOUT_SECONDS`, so a pull that hangs
  in this process fails itself and frees the slot.
* `reap_stale_jobs` fails rows whose owner is gone (OOM-killed worker, rolled
  deploy). It cannot rely on the owner to time itself out, so it also runs on a
  timer (`reap_forever`) instead of only at startup.
"""

import asyncio
import os
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select

from src.db.engine import get_session_factory
from src.db.models import PullJob as PullJobModel
from src.utils.helpers import utcnow

MAX_CONCURRENT_PULLS = int(os.getenv("MAX_CONCURRENT_PULLS", "2"))
STALE_JOB_MINUTES = int(os.getenv("STALE_JOB_MINUTES", "30"))
# Hard ceiling on one job. Kept under STALE_JOB_MINUTES so the in-process
# timeout wins over the reaper and reports the real reason. A slow source
# (EDGAR rate-limiting a cloud IP) otherwise runs for hours and blocks every
# later pull for the same key.
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", str(STALE_JOB_MINUTES * 60 - 300)))
REAP_INTERVAL_SECONDS = int(os.getenv("REAP_INTERVAL_SECONDS", "300"))
# ponytail: queue depth is the only abuse bound (no per-key quotas — rpm already
# caps submit rate); add per-key limits if a key ever floods the queue. The drain
# tick is a poll: upgrade to an asyncio.Event wake-up if 2s queue latency ever
# matters.
MAX_QUEUED_JOBS = int(os.getenv("MAX_QUEUED_JOBS", "20"))
DRAIN_INTERVAL_SECONDS = float(os.getenv("DRAIN_INTERVAL_SECONDS", "2"))

JOB_STATUSES = {"queued", "running", "done", "failed"}
ACTIVE_STATUSES = {"queued", "running"}

_inflight: set[str] = set()
_inflight_kinds: Dict[str, Optional[str]] = {}


class PullLimitReached(Exception):
    """Job queue full."""


class JobNotCancellable(Exception):
    """Job cannot be cancelled (unknown, or already finished)."""


def _pull_outcome(pull_result: Dict[str, Any]) -> str:
    """Job status ('done'/'failed') for a finished pull.

    A pull that ran but parsed/wrote zero records is a failure, not a silent
    success (e.g. NSE returning empty for a symbol, or SEC being unreachable).
    """
    rows = pull_result.get("rows_written") or pull_result.get("xbrl_parsed") or 0
    status = pull_result.get("status")
    if status == "failed" or (status == "partial" and rows == 0):
        return "failed"
    return "done"


async def reap_stale_jobs() -> None:
    cutoff = utcnow() - timedelta(minutes=STALE_JOB_MINUTES)
    factory = get_session_factory()
    async with factory() as session:
        # Only running rows are reaped, aged from started_at so time spent
        # queued does not eat the job's window. Queued rows hold no slot and
        # belong to the drain loop (cancel is the escape hatch), so an old
        # queued row is never failed here.
        result = await session.execute(
            select(PullJobModel).where(
                PullJobModel.status == "running",
                PullJobModel.started_at < cutoff,
            )
        )
        stale = list(result.scalars().all())
        for job in stale:
            job.status = "failed"
            job.error = "Job exceeded the stale window (worker restart or timeout). Re-run the pull."
            job.finished_at = utcnow()
            logger.warning(f"Reaped stale pull job {job.job_id} ({job.symbol})")
        await session.commit()


async def reap_forever() -> None:
    """Sweep hung running rows on a timer, not just at startup."""
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            await reap_stale_jobs()
        except Exception:
            logger.exception("Stale job sweep failed")


async def _active_counts(task: Optional[str] = None) -> Dict[str, int]:
    """Running/queued counts for one task kind (task=None → symbol pulls)."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(PullJobModel.status, func.count())
            .where(PullJobModel.status.in_(list(ACTIVE_STATUSES)))
            .group_by(PullJobModel.status)
        )
        if task:
            stmt = stmt.where(PullJobModel.task == task)
        else:
            stmt = stmt.where(PullJobModel.task.is_(None))
        return {status: n for status, n in (await session.execute(stmt)).all()}


async def _check_queue_capacity(task: Optional[str] = None) -> None:
    """Reject only when the queue for this task kind is full.

    Free slots are taken immediately; otherwise the job waits for the drain
    loop instead of erroring, so simultaneous submissions all complete.
    """
    counts = await _active_counts(task)
    if counts.get("queued", 0) >= MAX_QUEUED_JOBS:
        kind = task or "pull"
        raise PullLimitReached(
            f"{kind} queue is full ({counts.get('queued', 0)} >= {MAX_QUEUED_JOBS}). Try again shortly."
        )


def _running_count(task: Optional[str]) -> int:
    return sum(1 for kind in _inflight_kinds.values() if kind == task)


def _try_start(job: PullJobModel) -> bool:
    """Start a queued job iff it is not in flight and a slot is free.

    The checks and the add have no await between them, so on asyncio they are
    atomic — submit and the drain loop can race without double-starting a job
    or overshooting MAX_CONCURRENT_PULLS. ponytail: in-process only; a second
    replica would need a DB lease for both the inflight set and the cap.
    """
    if job.job_id in _inflight:
        return False
    if _running_count(job.task) >= MAX_CONCURRENT_PULLS:
        return False
    _inflight.add(job.job_id)
    _inflight_kinds[job.job_id] = job.task
    asyncio.create_task(_run_job(job))
    return True


async def requeue_orphaned_running() -> None:
    """At boot every 'running' row belongs to a dead process (single worker);
    requeue it so a Render deploy resumes interrupted pulls instead of leaving
    them to the reaper.
    ponytail: single-worker assumption — with multiple replicas this needs a
    real lease/heartbeat instead.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(PullJobModel.status == "running")
        )
        orphans = list(result.scalars().all())
        for job in orphans:
            job.status = "queued"
            job.started_at = None
        if orphans:
            await session.commit()
            logger.info(f"Re-queued {len(orphans)} orphaned running jobs at startup")


async def drain_forever() -> None:
    """Promote queued jobs into free slots every DRAIN_INTERVAL_SECONDS."""
    while True:
        await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
        try:
            await _drain_once()
        except Exception:
            logger.exception("Queue drain failed")


async def _drain_once() -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel)
            .where(PullJobModel.status == "queued")
            .order_by(PullJobModel.created_at)
        )
        queued = list(result.scalars().all())
    for job in queued:  # oldest first, per-kind FIFO; _try_start enforces slots
        _try_start(job)


async def submit_pull(
    symbol: str,
    filing_type: Optional[str],
    refresh: bool,
    created_by: Optional[str],
    country: str = "in",
    source: str = "nse",
) -> PullJobModel:
    await _check_queue_capacity()

    job = PullJobModel(
        job_id=str(uuid.uuid4()),
        symbol=symbol.upper(),
        filing_type=filing_type,
        refresh=refresh,
        created_by=created_by,
        country=country,
        source=source.upper(),
    )

    factory = get_session_factory()
    async with factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    if _try_start(job):
        logger.info(f"Pull job started: {job.job_id} for {job.symbol}")
    else:
        # same key twice (or all slots busy) → queued; both jobs run sequentially
        logger.info(f"Pull job queued: {job.job_id} for {job.symbol}")
    return job


async def submit_task(
    task: str,
    task_args: Dict[str, Any],
    created_by: Optional[str] = None,
) -> PullJobModel:
    """Submit a generic async analytics job (e.g. parse PDF)."""
    await _check_queue_capacity(task)

    symbol = task_args.get("symbol") or "*"
    job = PullJobModel(
        job_id=str(uuid.uuid4()),
        symbol=symbol,
        task=task,
        task_args=task_args,
        created_by=created_by,
    )

    factory = get_session_factory()
    async with factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    if _try_start(job):
        logger.info(f"Task job started: {job.job_id} ({task})")
    else:
        logger.info(f"Task job queued: {job.job_id} ({task})")
    return job


async def _run_job(job: PullJobModel) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(PullJobModel.id == job.id)
        )
        db_job = result.scalar_one()
        if db_job.status != "queued":
            # cancelled (or reaped) while waiting for a slot — _try_start has
            # already registered us as in flight, so release it and bail.
            _inflight.discard(job.job_id)
            _inflight_kinds.pop(job.job_id, None)
            return
        db_job.status = "running"
        db_job.started_at = utcnow()
        await session.commit()

        try:
            # ponytail: cancelling this frees the job row and the slot, but work
            # already offloaded to a thread (SEC/edgartools) keeps running until
            # edgartools' own request timeouts expire. Process-level kill if that
            # ever matters.
            pull_result = await asyncio.wait_for(
                _dispatch(db_job), timeout=JOB_TIMEOUT_SECONDS
            )
            db_job.result = pull_result
            db_job.status = _pull_outcome(pull_result)
            if db_job.status == "failed":
                db_job.error = (
                    f"Pull produced no records (status={pull_result.get('status')}); "
                    "source returned empty/unparseable data. Check source accessibility."
                )
        except asyncio.TimeoutError:
            logger.error(
                f"Job {db_job.job_id} ({db_job.task or db_job.symbol}) exceeded "
                f"{JOB_TIMEOUT_SECONDS}s; marking failed to free the slot"
            )
            db_job.status = "failed"
            db_job.error = (
                f"Timed out after {JOB_TIMEOUT_SECONDS}s. The source is too slow or "
                "unreachable (upstream rate-limiting). Re-run the pull."
            )
        except Exception as exc:
            logger.exception(f"Job {db_job.job_id} failed ({db_job.task or db_job.symbol})")
            db_job.error = str(exc)
            db_job.status = "failed"
        finally:
            db_job.finished_at = utcnow()
            await session.commit()
            _inflight.discard(db_job.job_id)
            _inflight_kinds.pop(db_job.job_id, None)

    # Optional webhook (audit P3-13): notify the submitter instead of polling.
    callback_url = (db_job.task_args or {}).get("callback_url") or (
        job.task_args or {}
    ).get("callback_url")
    if callback_url:
        await asyncio.to_thread(
            _post_callback,
            callback_url,
            {
                "event": "job.completed",
                "job_id": db_job.job_id,
                "task": db_job.task,
                "symbol": db_job.symbol,
                "status": db_job.status,
                "error": db_job.error,
                "finished_at": db_job.finished_at.isoformat()
                if db_job.finished_at
                else None,
            },
        )


def _post_callback(url: str, payload: Dict[str, Any]) -> None:
    """Fire-and-forget webhook; failures are logged, never raised."""
    import json as _json
    from urllib.request import Request, urlopen

    try:
        req = Request(  # noqa: S310
            url,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:  # noqa: S310
            logger.info(
                f"Callback for job {payload.get('job_id')} -> {url}: HTTP {resp.status}"
            )
    except Exception as exc:  # noqa: BLE001 - a webhook failure must not fail the job
        logger.warning(f"Callback to {url} failed for job {payload.get('job_id')}: {exc}")


async def _dispatch(db_job: PullJobModel) -> Dict[str, Any]:
    """Run the work for a job row (task, SEC pull, or NSE pull)."""
    if db_job.task:
        return await _run_task(db_job.task, db_job.task_args or {})
    if db_job.source == "SEC":
        from src.services.sec import pull_sec_data

        return await pull_sec_data(
            db_job.symbol, db_job.filing_type, db_job.refresh
        )
    from src.services import pull_nse_data

    return await pull_nse_data(db_job.symbol, db_job.filing_type, db_job.refresh)


async def _run_task(task: str, task_args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a named task to its handler module."""
    if task == "documents.parse":
        from src.services.documents import parse_document

        return await parse_document(task_args)
    raise ValueError(f"Unknown task: {task}")


async def get_job(job_id: str) -> Optional[PullJobModel]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(PullJobModel.job_id == job_id)
        )
        return result.scalar_one_or_none()


async def list_jobs(limit: int = 20) -> List[PullJobModel]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel)
            .order_by(PullJobModel.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())


async def cancel_job(job_id: str, created_by: Optional[str] = None) -> PullJobModel:
    """Cancel a queued/running job so it frees its concurrency slot.

    Escape hatch for the stuck-job case observed in the audit: a documents job
    hung in 'running' on Render blocked every later task for the key with 409s.
    In-process asyncio tasks cannot be force-killed across a restart, so this
    marks the row failed; when the orphaned task eventually finishes it finds a
    terminal row and its write is harmless. Only the owning key (or an
    already-anonymous job) may cancel.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(PullJobModel.job_id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError("not found")
        if job.status not in ACTIVE_STATUSES:
            raise JobNotCancellable(
                f"Job {job_id} is already '{job.status}' and cannot be cancelled."
            )
        if created_by and job.created_by and job.created_by != created_by:
            raise PermissionError("Job belongs to a different API key.")
        job.status = "failed"
        job.error = "Cancelled by request."
        job.finished_at = utcnow()
        await session.commit()
        logger.warning(f"Job {job_id} cancelled via API (by {created_by or 'unknown'})")
        return job

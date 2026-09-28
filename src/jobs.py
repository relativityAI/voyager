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
from sqlalchemy import select

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

JOB_STATUSES = {"queued", "running", "done", "failed"}
ACTIVE_STATUSES = {"queued", "running"}

_inflight: set[str] = set()


class PullLimitReached(Exception):
    """Global concurrency cap reached."""


class PullAlreadyActive(Exception):
    """This key already has a job of this type in progress."""


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
        result = await session.execute(
            select(PullJobModel).where(
                PullJobModel.status.in_(list(ACTIVE_STATUSES)),
                PullJobModel.created_at < cutoff,
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
    """Sweep orphaned jobs on a timer, not just at startup.

    A job whose worker was killed leaves a queued/running row that only a
    successful restart would clear, and until it is cleared every later pull
    for that key fails with 409.
    """
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            await reap_stale_jobs()
        except Exception:
            logger.exception("Stale job sweep failed")


async def _check_concurrency(created_by: Optional[str] = None, task: Optional[str] = None) -> None:
    """Enforce per-task-type slot limits.

    Slots are counted per task kind (symbol pull vs documents.parse), so one
    stuck documents job can no longer block a pull — the audit's P0 finding.
    """
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(PullJobModel).where(PullJobModel.status.in_(list(ACTIVE_STATUSES)))
        if task:
            stmt = stmt.where(PullJobModel.task == task)
        else:
            stmt = stmt.where(PullJobModel.task.is_(None))
        result = await session.execute(stmt)
        active = list(result.scalars().all())

    if len(active) >= MAX_CONCURRENT_PULLS:
        kind = task or "pull"
        raise PullLimitReached(
            f"Too many {kind} jobs in progress ({len(active)} >= {MAX_CONCURRENT_PULLS}). Try again shortly."
        )
    if created_by and any(j.created_by == created_by for j in active):
        kind = task or "pull"
        raise PullAlreadyActive(f"A {kind} job for this key is already in progress.")


async def submit_pull(
    symbol: str,
    filing_type: Optional[str],
    refresh: bool,
    created_by: Optional[str],
    country: str = "in",
    source: str = "nse",
) -> PullJobModel:
    await _check_concurrency(created_by)

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

    asyncio.create_task(_run_job(job))
    logger.info(f"Pull job queued: {job.job_id} for {job.symbol}")
    return job


async def submit_task(
    task: str,
    task_args: Dict[str, Any],
    created_by: Optional[str] = None,
) -> PullJobModel:
    """Submit a generic async analytics job (e.g. parse PDF)."""
    await _check_concurrency(created_by, task=task)

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

    asyncio.create_task(_run_job(job))
    logger.info(f"Task job queued: {job.job_id} ({task})")
    return job


async def _run_job(job: PullJobModel) -> None:
    _inflight.add(job.job_id)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(PullJobModel.id == job.id)
        )
        db_job = result.scalar_one()
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

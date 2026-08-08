"""Async pull jobs.

POST /pull is long-running (NSE XBRL fetch + parse can take 30-120s+) and
Render hard-timeouts web requests at ~60s. Instead of blocking, the endpoint
submits a job and returns 202 with a job_id; clients poll GET /pull/jobs/{id}.

Jobs run as in-process asyncio tasks, so a worker restart orphans them; the
startup sweep (`reap_stale_jobs`) fails any job stuck in queued/running.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from beanie import Document, Indexed
from loguru import logger
from pydantic import Field

from src.utils.helpers import utcnow

MAX_CONCURRENT_PULLS = int(os.getenv("MAX_CONCURRENT_PULLS", "2"))
STALE_JOB_MINUTES = int(os.getenv("STALE_JOB_MINUTES", "30"))

JOB_STATUSES = {"queued", "running", "done", "failed"}
ACTIVE_STATUSES = {"queued", "running"}

_inflight: set[str] = set()


class PullLimitReached(Exception):
    """Global concurrency cap reached."""


class PullAlreadyActive(Exception):
    """This key already has a pull in progress."""


class PullJob(Document):
    job_id: str = Indexed(unique=True)
    symbol: str
    country: str = "in"
    source: str = "nse"
    filing_type: Optional[str] = None
    refresh: bool = False
    status: str = "queued"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Settings:
        name = "pull_jobs"
        indexes = ["job_id", "created_at", "status"]

    def to_public_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "symbol": self.symbol,
            "filing_type": self.filing_type,
            "refresh": self.refresh,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "result": self.result,
            "error": self.error,
        }


async def reap_stale_jobs() -> None:
    """Fail jobs stuck in queued/running past the stale window."""
    cutoff = utcnow() - timedelta(minutes=STALE_JOB_MINUTES)
    stale = await PullJob.find(
        {"status": {"$in": list(ACTIVE_STATUSES)}, "created_at": {"$lt": cutoff}}
    ).to_list()
    for job in stale:
        job.status = "failed"
        job.error = "Job exceeded the stale window (worker restart or timeout). Re-run the pull."
        job.finished_at = utcnow()
        await job.save()
        logger.warning(f"Reaped stale pull job {job.job_id} ({job.symbol})")


async def submit_pull(
    symbol: str,
    filing_type: Optional[str],
    refresh: bool,
    created_by: Optional[str],
) -> PullJob:
    """Queue a pull. Raises PullLimitReached / PullAlreadyActive on limits."""
    active = await PullJob.find({"status": {"$in": list(ACTIVE_STATUSES)}}).to_list()
    if len(active) >= MAX_CONCURRENT_PULLS:
        raise PullLimitReached(
            f"Too many pulls in progress ({len(active)} >= {MAX_CONCURRENT_PULLS}). Try again shortly."
        )
    if created_by and any(j.created_by == created_by for j in active):
        raise PullAlreadyActive("A pull for this key is already in progress.")

    job = PullJob(
        job_id=str(uuid.uuid4()),
        symbol=symbol.upper(),
        filing_type=filing_type,
        refresh=refresh,
        created_by=created_by,
    )
    await job.insert()
    asyncio.create_task(_run_job(job))
    logger.info(f"Pull job queued: {job.job_id} for {job.symbol}")
    return job


async def _run_job(job: PullJob) -> None:
    _inflight.add(job.job_id)
    job.status = "running"
    job.started_at = utcnow()
    await job.save()
    try:
        from src.services import pull_nse_data

        result = await pull_nse_data(job.symbol, job.filing_type, job.refresh)
        job.result = result
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the job record
        logger.exception(f"Pull job {job.job_id} failed for {job.symbol}")
        job.error = str(exc)
        job.status = "failed"
    finally:
        job.finished_at = utcnow()
        await job.save()
        _inflight.discard(job.job_id)


async def get_job(job_id: str) -> Optional[PullJob]:
    return await PullJob.find_one(PullJob.job_id == job_id)


async def list_jobs(limit: int = 20) -> List[PullJob]:
    return (
        await PullJob.find()
        .sort("-created_at")
        .limit(max(1, min(limit, 100)))
        .to_list()
    )

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

from loguru import logger
from sqlalchemy import select, update

from src.db.engine import get_session_factory
from src.db.models import PullJob as PullJobModel
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


async def submit_pull(
    symbol: str,
    filing_type: Optional[str],
    refresh: bool,
    created_by: Optional[str],
) -> PullJobModel:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PullJobModel).where(
                PullJobModel.status.in_(list(ACTIVE_STATUSES))
            )
        )
        active = list(result.scalars().all())

    if len(active) >= MAX_CONCURRENT_PULLS:
        raise PullLimitReached(
            f"Too many pulls in progress ({len(active)} >= {MAX_CONCURRENT_PULLS}). Try again shortly."
        )
    if created_by and any(j.created_by == created_by for j in active):
        raise PullAlreadyActive("A pull for this key is already in progress.")

    job = PullJobModel(
        job_id=str(uuid.uuid4()),
        symbol=symbol.upper(),
        filing_type=filing_type,
        refresh=refresh,
        created_by=created_by,
    )

    factory = get_session_factory()
    async with factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    asyncio.create_task(_run_job(job))
    logger.info(f"Pull job queued: {job.job_id} for {job.symbol}")
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
            from src.services import pull_nse_data, pull_market_data

            pull_result = await pull_nse_data(db_job.symbol, db_job.filing_type, db_job.refresh)

            # Market data is a best-effort add-on; never break the main pull.
            try:
                market_result = await pull_market_data(db_job.symbol)
                pull_result["market_data"] = market_result
            except Exception as market_exc:
                logger.warning(f"Market data pull failed for {db_job.symbol}: {market_exc}")
                pull_result["market_data"] = {"error": str(market_exc)}

            db_job.result = pull_result
            db_job.status = "done"
        except Exception as exc:
            logger.exception(f"Pull job {db_job.job_id} failed for {db_job.symbol}")
            db_job.error = str(exc)
            db_job.status = "failed"
        finally:
            db_job.finished_at = utcnow()
            await session.commit()
            _inflight.discard(db_job.job_id)


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

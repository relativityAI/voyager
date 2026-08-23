"""Read-only stats queries against PostgreSQL (Supabase).

The panel connects straight to the database for the Database Stats tab. Everything is
wrapped so an unreachable database degrades to an error message instead of
crashing the app.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from src.db.models import (
    IncomeStatement,
    BalanceSheet,
    CashFlow,
    Shareholding,
    NSEStockMetadata,
    APIKey,
    PullJob,
)

STATEMENT_TABLES = ["income_statements", "balance_sheets", "cash_flows"]
ALL_TABLES = STATEMENT_TABLES + [
    "shareholdings",
    "nse_stock_metadata",
    "api_keys",
    "pull_jobs",
]

TABLE_MODELS = {
    "income_statements": IncomeStatement,
    "balance_sheets": BalanceSheet,
    "cash_flows": CashFlow,
    "shareholdings": Shareholding,
    "nse_stock_metadata": NSEStockMetadata,
    "api_keys": APIKey,
    "pull_jobs": PullJob,
}


class DBError(Exception):
    pass


def connect(url: str):
    if not url:
        raise DBError("No DATABASE_URL configured.")
    db_url = make_url(url)
    if db_url.drivername == "postgresql+asyncpg":
        # This module is sync; asyncpg only works with asyncio engines.
        db_url = db_url.set(drivername="postgresql+psycopg2")
    try:
        engine = create_engine(
            db_url,
            pool_size=2,
            max_overflow=1,
            pool_pre_ping=True,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        raise DBError(f"Could not reach PostgreSQL: {exc}") from exc


def safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DBError:
            raise
        except Exception as exc:
            return {"error": str(exc)}

    return wrapper


@safe
def server_info(engine) -> dict:
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        table_count = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar()
        total_rows = 0
        for tname in ALL_TABLES:
            try:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {tname}")).scalar()
                total_rows += cnt or 0
            except Exception:
                pass
    return {
        "server_version": version or "?",
        "engine": "PostgreSQL",
        "db_name": "postgres",
        "tables": table_count or 0,
        "total_rows": total_rows,
    }


@safe
def collection_table(engine) -> List[dict]:
    rows = []
    with engine.connect() as conn:
        for name in ALL_TABLES:
            try:
                stats = conn.execute(
                    text(f"SELECT pg_total_relation_size('{name}') as total_size, "
                         f"pg_relation_size('{name}') as data_size, "
                         f"(SELECT COUNT(*) FROM {name}) as cnt")
                ).fetchone()
                rows.append({
                    "collection": name,
                    "documents": stats.cnt if stats else 0,
                    "size_mb": round((stats.total_size or 0) / 1048576, 3) if stats else 0,
                    "data_mb": round((stats.data_size or 0) / 1048576, 3) if stats else 0,
                })
            except Exception as exc:
                rows.append({"collection": name, "documents": 0, "error": str(exc)})
    return rows


@safe
def collection_detail(engine, name: str) -> dict:
    model = TABLE_MODELS.get(name)
    if not model:
        return {"collection": name, "error": f"Unknown table: {name}"}

    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0

        coverage = {}
        if name in STATEMENT_TABLES:
            result = conn.execute(
                text(f"SELECT MIN(period_end_date) as min_p, MAX(period_end_date) as max_p, "
                     f"COUNT(DISTINCT period_end_date) as dist_p FROM {name}")
            ).fetchone()
            if result:
                coverage = {
                    "min_period": str(result.min_p or ""),
                    "max_period": str(result.max_p or ""),
                    "distinct_periods": result.dist_p or 0,
                }

        top_symbols = []
        try:
            result = conn.execute(
                text(f"SELECT symbol, COUNT(*) as cnt FROM {name} "
                     f"GROUP BY symbol ORDER BY cnt DESC LIMIT 15")
            )
            top_symbols = [{"symbol": r.symbol, "docs": r.cnt} for r in result]
        except Exception:
            pass

        filing_types = {}
        try:
            result = conn.execute(
                text(f"SELECT filing_type, COUNT(*) as cnt FROM {name} GROUP BY filing_type")
            )
            filing_types = {r.filing_type: r.cnt for r in result}
        except Exception:
            pass

        sample = None
        try:
            result = conn.execute(text(f"SELECT * FROM {name} LIMIT 1"))
            row = result.fetchone()
            if row:
                sample = dict(row._mapping)
        except Exception:
            pass

    return {
        "collection": name,
        "documents": count,
        "coverage": coverage,
        "top_symbols": top_symbols,
        "filing_types": filing_types,
        "sample_doc": sample,
    }


@safe
def job_stats(engine) -> dict:
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM pull_jobs")).scalar() or 0

        status_counts = {}
        result = conn.execute(
            text("SELECT status, COUNT(*) as cnt FROM pull_jobs GROUP BY status")
        )
        for r in result:
            status_counts[r.status or "unknown"] = r.cnt

        per_day = []
        try:
            result = conn.execute(
                text("SELECT DATE(created_at) as d, COUNT(*) as cnt "
                     "FROM pull_jobs GROUP BY DATE(created_at) ORDER BY d DESC LIMIT 60")
            )
            per_day = [{"date": str(r.d), "jobs": r.cnt} for r in result]
        except Exception:
            pass

        durations = []
        try:
            result = conn.execute(
                text("SELECT started_at, finished_at FROM pull_jobs "
                     "WHERE started_at IS NOT NULL AND finished_at IS NOT NULL")
            )
            for r in result:
                try:
                    secs = (r.finished_at - r.started_at).total_seconds()
                    durations.append(secs)
                except (TypeError, AttributeError):
                    pass
        except Exception:
            pass

        recent_failed = []
        try:
            result = conn.execute(
                text("SELECT * FROM pull_jobs WHERE status = 'failed' "
                     "ORDER BY created_at DESC LIMIT 10")
            )
            recent_failed = [dict(r._mapping) for r in result]
        except Exception:
            pass

    return {
        "total": total,
        "by_status": status_counts,
        "per_day": per_day,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
        "recent_failed": recent_failed,
    }


@safe
def key_stats(engine) -> dict:
    with engine.connect() as conn:
        now = datetime.now(timezone.utc)
        total = conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar() or 0
        enabled = conn.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE enabled = true")
        ).scalar() or 0
        revoked = conn.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NOT NULL")
        ).scalar() or 0
        expired = conn.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE expires_at < :now"),
            {"now": now},
        ).scalar() or 0

        scopes = {}
        try:
            result = conn.execute(
                text("SELECT unnest(scopes) as scope, COUNT(*) as cnt "
                     "FROM api_keys GROUP BY unnest(scopes)")
            )
            scopes = {r.scope: r.cnt for r in result}
        except Exception:
            pass

    return {
        "total": total,
        "enabled": enabled,
        "revoked": revoked,
        "expired": expired,
        "scopes": scopes,
    }


@safe
def distinct_symbols(engine) -> List[str]:
    symbols = set()
    with engine.connect() as conn:
        for name in STATEMENT_TABLES + ["shareholdings"]:
            try:
                result = conn.execute(
                    text(f"SELECT DISTINCT symbol FROM {name}")
                )
                for r in result:
                    if isinstance(r.symbol, str):
                        symbols.add(r.symbol)
            except Exception:
                pass
    return sorted(symbols)

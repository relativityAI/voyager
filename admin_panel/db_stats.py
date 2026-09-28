"""Read-only stats queries against PostgreSQL.

The table list comes from the ORM's metadata rather than a hand-kept
constant, which had drifted to 7 of the 13 tables and made the "Tables" and
"Rows" metrics disagree with each other.

Every query that can fail on one specific table records why, instead of
swallowing it into a zero that reads exactly like an empty table.
"""

import re
from typing import List

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from src.db import models as db_models  # noqa: F401 - importing registers every table
from src.db.engine import Base

STATEMENT_TABLES = ["income_statements", "balance_sheets", "cash_flows"]
ALL_TABLES = [t.name for t in Base.metadata.sorted_tables]

# Never rendered, and a hash of a high-entropy token is still a secret.
REDACTED = {"key_hash"}
_VERSION_RE = re.compile(r"PostgreSQL\s+([\d.]+)")


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
        engine = create_engine(db_url, pool_size=2, max_overflow=1, pool_pre_ping=True)
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
def snapshot(engine) -> dict:
    """Every section the Database Stats page renders, in one go."""
    return {
        "server": server_info(engine),
        "collections": collection_table(engine),
        "coverage": metrics_field_coverage(engine),
        "jobs": job_stats(engine),
        "keys": key_stats(engine),
    }


@safe
def server_info(engine) -> dict:
    errors: list[str] = []
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT version()")).scalar() or ""
        table_count = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar()
        db_name = conn.execute(text("SELECT current_database()")).scalar() or "?"
        total_rows = 0
        for tname in ALL_TABLES:
            try:
                total_rows += conn.execute(text(f"SELECT COUNT(*) FROM {tname}")).scalar() or 0
            except Exception as exc:
                errors.append(f"{tname}: {exc}")
    match = _VERSION_RE.search(raw)
    return {
        "server_version": match.group(1) if match else raw[:40],
        "engine": "PostgreSQL",
        "db_name": db_name,
        "tables": table_count or 0,
        "total_rows": total_rows,
        "errors": errors,
    }


@safe
def collection_table(engine) -> List[dict]:
    rows = []
    with engine.connect() as conn:
        for name in ALL_TABLES:
            try:
                stats = conn.execute(
                    text(
                        f"SELECT pg_total_relation_size('{name}') AS total_size, "
                        f"pg_relation_size('{name}') AS data_size, "
                        f"(SELECT COUNT(*) FROM {name}) AS cnt"
                    )
                ).fetchone()
                rows.append(
                    {
                        "collection": name,
                        "documents": stats.cnt if stats else 0,
                        "size_mb": round((stats.total_size or 0) / 1048576, 3) if stats else 0,
                        "data_mb": round((stats.data_size or 0) / 1048576, 3) if stats else 0,
                    }
                )
            except Exception as exc:
                rows.append({"collection": name, "documents": 0, "error": str(exc)})
    return rows


@safe
def collection_detail(engine, name: str) -> dict:
    if name not in ALL_TABLES:
        return {"collection": name, "error": f"Unknown table: {name}"}

    notes: list[str] = []
    with engine.connect() as conn:
        try:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0
        except Exception as exc:
            return {"collection": name, "error": str(exc)}

        coverage = {}
        if name in STATEMENT_TABLES:
            result = conn.execute(
                text(
                    f"SELECT MIN(period_end_date) AS min_p, MAX(period_end_date) AS max_p, "
                    f"COUNT(DISTINCT period_end_date) AS dist_p FROM {name}"
                )
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
                text(f"SELECT symbol, COUNT(*) AS cnt FROM {name} "
                     f"GROUP BY symbol ORDER BY cnt DESC LIMIT 15")
            )
            top_symbols = [{"symbol": r.symbol, "docs": r.cnt} for r in result]
        except Exception as exc:
            notes.append(f"symbol breakdown unavailable: {exc}")

        filing_types = {}
        try:
            result = conn.execute(
                text(f"SELECT filing_type, COUNT(*) AS cnt FROM {name} GROUP BY filing_type")
            )
            filing_types = {r.filing_type: r.cnt for r in result}
        except Exception as exc:
            notes.append(f"filing_type breakdown unavailable: {exc}")

        sample = None
        try:
            row = conn.execute(text(f"SELECT * FROM {name} LIMIT 1")).fetchone()
            if row:
                sample = {k: v for k, v in row._mapping.items() if k not in REDACTED}
        except Exception as exc:
            notes.append(f"sample document unavailable: {exc}")

    return {
        "collection": name,
        "documents": count,
        "coverage": coverage,
        "top_symbols": top_symbols,
        "filing_types": filing_types,
        "sample_doc": sample,
        "notes": notes,
    }


@safe
def metrics_field_coverage(engine) -> List[dict]:
    """Coverage of the financial-metrics source fields per statement table.

    0% on these means a re-pull with refresh=true is needed before the ratios
    that read them can appear in /financial-metrics.
    """
    checks = [
        ("balance_sheets", "assets_current"),
        ("balance_sheets", "inventories"),
        ("balance_sheets", "trade_receivables_current"),
        ("balance_sheets", "trade_payables"),
        ("balance_sheets", "current_liabilities"),
        ("balance_sheets", "bank_balance_other_than_cash_and_cash_equivalents"),
        ("cash_flows", "dividends_paid"),
        ("cash_flows", "payments_for_purchase_of_noncurrent_assets"),
        ("income_statements", "cost_of_revenue"),
        ("income_statements", "expenses"),
    ]
    rows = []
    with engine.connect() as conn:
        for table, field in checks:
            try:
                r = conn.execute(
                    text(f"SELECT COUNT(*) AS total, COUNT({field}) AS filled FROM {table}")
                ).fetchone()
                filled, total = r.filled or 0, r.total or 0
                rows.append(
                    {
                        "table": table,
                        "field": field,
                        "filled": filled,
                        "total": total,
                        "coverage_pct": round(filled / total * 100, 1) if total else 0.0,
                    }
                )
            except Exception as exc:
                rows.append({"table": table, "field": field, "error": str(exc)})
    return rows


@safe
def job_stats(engine) -> dict:
    errors: list[str] = []
    with engine.connect() as conn:
        try:
            total = conn.execute(text("SELECT COUNT(*) FROM pull_jobs")).scalar() or 0
        except Exception as exc:
            return {"error": f"pull_jobs: {exc}"}

        status_counts = {}
        for r in conn.execute(text("SELECT status, COUNT(*) AS cnt FROM pull_jobs GROUP BY status")):
            status_counts[r.status or "unknown"] = r.cnt

        per_day = []
        try:
            result = conn.execute(
                text(
                    "SELECT DATE(created_at) AS d, COUNT(*) AS cnt FROM pull_jobs "
                    "GROUP BY DATE(created_at) ORDER BY d ASC LIMIT 60"
                )
            )
            per_day = [{"date": str(r.d), "jobs": r.cnt} for r in result]
        except Exception as exc:
            errors.append(f"jobs per day: {exc}")

        durations = []
        try:
            for r in conn.execute(
                text("SELECT started_at, finished_at FROM pull_jobs "
                     "WHERE started_at IS NOT NULL AND finished_at IS NOT NULL")
            ):
                try:
                    durations.append((r.finished_at - r.started_at).total_seconds())
                except (TypeError, AttributeError):
                    pass
        except Exception as exc:
            errors.append(f"durations: {exc}")

        recent_failed = []
        try:
            result = conn.execute(
                text("SELECT * FROM pull_jobs WHERE status = 'failed' "
                     "ORDER BY created_at DESC LIMIT 10")
            )
            recent_failed = [dict(r._mapping) for r in result]
        except Exception as exc:
            errors.append(f"recent failures: {exc}")

    return {
        "total": total,
        "by_status": status_counts,
        "per_day": per_day,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
        "recent_failed": recent_failed,
        "errors": errors,
    }


@safe
def key_stats(engine) -> dict:
    errors: list[str] = []
    with engine.connect() as conn:
        def count(sql: str) -> int:
            return conn.execute(text(sql)).scalar() or 0

        try:
            stats = {
                "total": count("SELECT COUNT(*) FROM api_keys"),
                "enabled": count("SELECT COUNT(*) FROM api_keys WHERE enabled = true"),
                "revoked": count("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NOT NULL"),
                # NOW() is the same clock the column was written with. Passing a
                # tz-aware Python value against this naive column made the
                # result depend on the server's session TimeZone.
                "expired": count("SELECT COUNT(*) FROM api_keys WHERE expires_at < NOW()"),
            }
        except Exception as exc:
            return {"error": f"api_keys: {exc}"}

        scopes: dict[str, int] = {}
        try:
            for r in conn.execute(
                text("SELECT unnest(scopes) AS scope, COUNT(*) AS cnt "
                     "FROM api_keys GROUP BY unnest(scopes)")
            ):
                scopes[r.scope] = r.cnt
        except Exception as exc:
            errors.append(f"scope breakdown: {exc}")

    return {**stats, "scopes": scopes, "errors": errors}


@safe
def distinct_symbols(engine) -> List[str]:
    symbols = set()
    with engine.connect() as conn:
        for name in STATEMENT_TABLES + ["shareholdings"]:
            try:
                for r in conn.execute(text(f"SELECT DISTINCT symbol FROM {name}")):
                    if isinstance(r.symbol, str):
                        symbols.add(r.symbol)
            except Exception:
                continue  # table may not exist yet; autocomplete is best-effort
    return sorted(symbols)

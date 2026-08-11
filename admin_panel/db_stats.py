"""Read-only stats queries against MongoDB Atlas.

The panel connects straight to Atlas for the Database Stats tab. Everything is
wrapped so an unreachable cluster degrades to an error message instead of
crashing the app. Uses a short server-selection timeout so a dead network
fails fast instead of hanging the tab.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, List

import certifi
from pymongo import MongoClient

STATEMENT_COLLECTIONS = ["income_statements", "balance_sheets", "cash_flows"]
ALL_COLLECTIONS = STATEMENT_COLLECTIONS + [
    "shareholdings",
    "nse_stock_metadata",
    "api_keys",
    "pull_jobs",
]

SELECT_TIMEOUT_MS = 8000


class DBError(Exception):
    pass


def connect(url: str, db_name: str = "voyager") -> tuple[MongoClient, Any]:
    if not url:
        raise DBError("No MONGODB_URL configured.")
    kwargs = dict(
        serverSelectionTimeoutMS=SELECT_TIMEOUT_MS,
        connectTimeoutMS=5000,
    )
    if url.startswith("mongodb+srv://"):
        kwargs["tlsCAFile"] = certifi.where()
    try:
        client = MongoClient(url, **kwargs)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - surface a friendly message
        raise DBError(
            f"Could not reach MongoDB: {exc}"
        ) from exc
    return client, client[db_name]


def safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DBError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade to error message
            return {"error": str(exc)}

    return wrapper


@safe
def server_info(db: Any) -> dict:
    client = db.client
    info = client.server_info()
    stats = db.command("dbStats")
    return {
        "server_version": info.get("version", "?"),
        "engine": info.get("storageEngine", {}).get("name", "?"),
        "db_name": db.name,
        "collections": stats.get("collections", 0),
        "total_documents": stats.get("objects", 0),
        "data_size_mb": round((stats.get("dataSize", 0) or 0) / 1048576, 2),
        "storage_size_mb": round((stats.get("storageSize", 0) or 0) / 1048576, 2),
        "index_size_mb": round((stats.get("indexSize", 0) or 0) / 1048576, 2),
    }


@safe
def collection_table(db: Any) -> List[dict]:
    names = sorted(
        c for c in db.list_collection_names() if c in ALL_COLLECTIONS
    )
    rows = []
    for name in names:
        stats = db.command("collStats", name)
        rows.append(
            {
                "collection": name,
                "documents": stats.get("count", 0),
                "avg_obj_size_b": stats.get("avgObjSize", 0),
                "size_mb": round((stats.get("size", 0) or 0) / 1048576, 3),
                "indexes": stats.get("nindexes", 0),
                "index_size_mb": round((stats.get("totalIndexSize", 0) or 0) / 1048576, 2),
            }
        )
    return rows


@safe
def collection_detail(db: Any, name: str) -> dict:
    coll = db[name]
    count = coll.estimated_document_count()

    coverage = {}
    if name in STATEMENT_COLLECTIONS:
        pipeline = [
            {"$group": {"_id": None, "min": {"$min": "$period_end_date"},
                        "max": {"$max": "$period_end_date"},
                        "distinct_periods": {"$addToSet": "$period_end_date"}}},
        ]
        groups = list(coll.aggregate(pipeline, allowDiskUse=True))
        if groups:
            g = groups[0]
            coverage = {
                "min_period": str(g.get("min", "")),
                "max_period": str(g.get("max", "")),
                "distinct_periods": len(g.get("distinct_periods", [])),
            }

    top_symbols = [
        {"symbol": doc["_id"], "docs": doc["count"]}
        for doc in coll.aggregate(
            [{"$group": {"_id": "$symbol", "count": {"$sum": 1}}},
             {"$sort": {"count": -1}},
             {"$limit": 15}]
        )
    ]

    filing_types = defaultdict(int)
    for doc in coll.aggregate(
        [{"$group": {"_id": "$filing_type", "count": {"$sum": 1}}}]
    ):
        filing_types[doc["_id"]] = doc["count"]

    sample = list(coll.find({}, {"_id": 0}).limit(1))
    return {
        "collection": name,
        "documents": count,
        "coverage": coverage,
        "top_symbols": top_symbols,
        "filing_types": dict(filing_types),
        "sample_doc": sample[0] if sample else None,
    }


@safe
def job_stats(db: Any) -> dict:
    jobs = db["pull_jobs"]
    status_counts = defaultdict(int)
    for doc in jobs.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]):
        status_counts[doc["_id"] or "unknown"] = doc["count"]

    per_day = [
        {"date": doc["_id"], "jobs": doc["count"]}
        for doc in jobs.aggregate(
            [
                {"$group": {"_id": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "count": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
                {"$limit": 60},
            ]
        )
    ]

    durations = []
    for doc in jobs.find(
        {"started_at": {"$ne": None}, "finished_at": {"$ne": None}},
        {"started_at": 1, "finished_at": 1},
    ):
        try:
            secs = (doc["finished_at"] - doc["started_at"]).total_seconds()
            durations.append(secs)
        except (TypeError, KeyError):
            pass

    recent_failed = list(
        jobs.find({"status": "failed"}, {"_id": 0})
        .sort("created_at", -1)
        .limit(10)
    )

    return {
        "total": jobs.estimated_document_count(),
        "by_status": dict(status_counts),
        "per_day": per_day,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
        "recent_failed": recent_failed,
    }


@safe
def key_stats(db: Any) -> dict:
    keys = db["api_keys"]
    now = datetime.now(timezone.utc)
    total = keys.estimated_document_count()
    enabled = keys.count_documents({"enabled": True})
    revoked = keys.count_documents({"revoked_at": {"$ne": None}})
    expired = keys.count_documents({"expires_at": {"$lt": now}})

    scopes = defaultdict(int)
    for doc in keys.aggregate([{"$unwind": "$scopes"},
                               {"$group": {"_id": "$scopes", "count": {"$sum": 1}}}]):
        scopes[doc["_id"]] = doc["count"]

    return {
        "total": total,
        "enabled": enabled,
        "revoked": revoked,
        "expired": expired,
        "scopes": dict(scopes),
    }


@safe
def distinct_symbols(db: Any) -> List[str]:
    symbols = set()
    for name in STATEMENT_COLLECTIONS + ["shareholdings"]:
        for doc in db[name].distinct("symbol"):
            if isinstance(doc, str):
                symbols.add(doc)
    return sorted(symbols)

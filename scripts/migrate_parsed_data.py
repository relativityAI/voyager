import asyncio
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, ".")

from src.db.connection import get_database
from src.tools.nse.ratios import FINANCIAL_FIELD_MAP
from src.utils.case_converter import camel_to_snake

OLD_COLLECTIONS = [
    "nse_quarterly_financials",
    "nse_annual_financials",
    "nse_shareholding_financials",
]

CATEGORY_TO_COLLECTION = {
    "income_statement": "income_statements",
    "balance_sheet": "balance_sheets",
    "cash_flow": "cash_flows",
    "shareholding": "shareholdings",
    "per_share": "income_statements",
    "metadata": None,
}

BATCH_SIZE = 100


def _classify(tag_camel: str) -> Optional[str]:
    field = FINANCIAL_FIELD_MAP.get(tag_camel)
    if field is None:
        return "income_statements"
    cat = field.get("category", "")
    return CATEGORY_TO_COLLECTION.get(cat, "income_statements")


async def migrate_collection(db, old_name: str) -> int:
    old_coll = db[old_name]
    total = await old_coll.count_documents({})
    if total == 0:
        print(f"  {old_name}: empty, skipping")
        return 0

    print(f"  {old_name}: {total} records")
    migrated = 0
    batch: Dict[str, List[Dict]] = {
        "income_statements": [],
        "balance_sheets": [],
        "cash_flows": [],
        "shareholdings": [],
    }

    cursor = old_coll.find({}).sort("date", -1)
    async for old_doc in cursor:
        financials = old_doc.get("financials", [])
        if not financials:
            continue

        symbol = old_doc.get("symbol", "")
        period_end = old_doc.get("date") or old_doc.get("period_end_date")
        if not period_end or not symbol:
            continue

        cons_raw = old_doc.get("consolidated", "Consolidated")
        is_cons = str(cons_raw).lower() in ("consolidated", "true", "1")

        base = {
            "symbol": symbol,
            "period_end_date": period_end,
            "consolidated": is_cons,
            "source_endpoint": old_doc.get("source_endpoint", old_name),
            "xbrl_url": old_doc.get("xbrl") or old_doc.get("xbrl_url"),
            "broadcast_date": old_doc.get("broadcast_date"),
            "pulled_at": old_doc.get("pulled_at", datetime.utcnow()),
        }

        docs: Dict[str, Dict] = {}
        ctx_types: Set[str] = set()

        for fact in financials:
            tag = fact.get("tag")
            value = fact.get("value")
            if not tag or value is None:
                continue
            cr = fact.get("contextRef", "")
            if cr:
                if "OneI" in cr or "OneD" in cr:
                    ctx_types.add("quarterly")
                elif "FourD" in cr:
                    ctx_types.add("annual")
                else:
                    ctx_types.add(cr)

            coll_name = _classify(tag)
            if coll_name is None:
                continue
            if coll_name not in docs:
                docs[coll_name] = dict(base)
            docs[coll_name][camel_to_snake(tag)] = value

        if not docs:
            continue

        ctx_str = ", ".join(sorted(ctx_types)) if ctx_types else base.get("source_endpoint", "")
        for coll_name, doc in docs.items():
            doc["context_ref_type"] = ctx_str
            if len(doc) > 11:
                batch[coll_name].append(doc)

        migrated += len(docs)

        if sum(len(v) for v in batch.values()) >= BATCH_SIZE:
            await _flush(db, batch)
            batch = {k: [] for k in batch}
            print(f"    ... {migrated} docs buffered")

    await _flush(db, batch)
    print(f"  {old_name}: done — {migrated} docs")
    return migrated


async def _flush(db, batch: Dict[str, List[Dict]]):
    for coll_name, docs in batch.items():
        if not docs:
            continue
        coll = db[coll_name]
        for doc in docs:
            await coll.replace_one(
                {
                    "symbol": doc["symbol"],
                    "period_end_date": doc["period_end_date"],
                    "consolidated": doc["consolidated"],
                    "source_endpoint": doc.get("source_endpoint"),
                },
                doc,
                upsert=True,
            )


async def main():
    db = get_database()
    total = 0
    print("Migrating old parsed collections to new per-statement collections…")
    for old_name in OLD_COLLECTIONS:
        n = await migrate_collection(db, old_name)
        total += n
    print(f"\nDone. {total} statement docs written to new collections.")
    for coll_name in ("income_statements", "balance_sheets", "cash_flows", "shareholdings"):
        cnt = await db[coll_name].count_documents({})
        print(f"  {coll_name}: {cnt}")


if __name__ == "__main__":
    asyncio.run(main())

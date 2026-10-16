"""Shared helpers for the ingestion pipeline (moved from services.nse)."""

from datetime import date, datetime
from typing import Any, Dict

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db.models import IncomeStatement


def _parse_date(date_str):
    if date_str is None:
        return None
    if isinstance(date_str, (date, datetime)):
        return date_str if isinstance(date_str, date) else date_str.date()
    try:
        return datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str):
    if dt_str is None:
        return None
    if isinstance(dt_str, datetime):
        return dt_str
    try:
        return datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(dt_str), "%d-%b-%Y %H:%M:%S")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(dt_str), "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_numeric(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    try:
        cleaned = str(val).replace(",", "")
        if cleaned == "" or cleaned.lower() == "null" or cleaned.lower() == "none":
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _doc_to_row(doc: Dict[str, Any], model_class) -> Dict[str, Any]:
    row = {}
    for col in model_class.__table__.columns:
        key = col.key
        if key == "id":
            continue
        if key in doc:
            val = doc[key]
            if key in ("period_end_date", "period_start_date"):
                row[key] = _parse_date(val)
            elif key == "pulled_at":
                row[key] = _parse_datetime(val) if isinstance(val, str) else val
            elif key in ("consolidated", "refresh", "enabled", "has_xbrl", "is_admin"):
                row[key] = val
            elif key == "_content_hash":
                row[key] = val
            elif key in ("previous_pulls",):
                row[key] = val or []
            elif key == "scopes":
                row[key] = val or []
            else:
                row[key] = _parse_numeric(val) if col.type.__class__.__name__ == "Numeric" else val

    # NSE reports `expenses` as total expenses including cost of goods, so
    # revenue - expenses is operating profit before other income. Verified
    # against the identity revenue - expenses + other_income == PBE, which
    # holds for every NSE row we store. SEC files report opex only, so they
    # take us-gaap_OperatingIncomeLoss instead (see _INCOME_MAP in sec.py).
    if model_class.__tablename__ == "income_statements":
        rev, exp = row.get("revenue_from_operations"), row.get("expenses")
        if rev is not None and exp is not None and row.get("operating_income") is None:
            row["operating_income"] = rev - exp

    return row


async def _upsert_rows(session, model_class, rows: list, on_conflict_cols: list):
    if not rows:
        return
    for row_data in rows:
        update = {
            k: v
            for k, v in row_data.items()
            if k not in ("id",) + tuple(on_conflict_cols)
        }
        stmt = pg_insert(model_class).values(**row_data)
        stmt = (
            stmt.on_conflict_do_nothing(index_elements=on_conflict_cols)
            if not update
            else stmt.on_conflict_do_update(
                index_elements=on_conflict_cols, set_=update
            )
        )
        await session.execute(stmt)

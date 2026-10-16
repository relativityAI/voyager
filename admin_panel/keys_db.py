"""Direct-DB API-key management for the admin panel (sync).

The HTTP admin router is gone; the panel talks to PostgreSQL through the same
rules the operator tooling uses (src.auth.models for key
generation/hashing, VALID_SCOPES for validation) so there is one key-CRUD
implementation. Sync engine like db_stats.py — Streamlit is sync.
"""

from datetime import timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from src.auth.models import VALID_SCOPES, generate_api_key, hash_key
from src.utils.helpers import utcnow

from .db_stats import DBError

_COLUMNS = (
    "id, name, owner, label, prefix, scopes, rpm, enabled, revoked_at, "
    "expires_at, last_used_at, created_at"
)


def connect(url: str):
    if not url:
        raise DBError("No DATABASE_URL configured.")
    db_url = make_url(url)
    if db_url.drivername == "postgresql+asyncpg":
        db_url = db_url.set(drivername="postgresql+psycopg2")
    try:
        engine = create_engine(db_url, pool_size=2, max_overflow=1, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        raise DBError(f"Could not reach PostgreSQL: {exc}") from exc


def list_keys(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {_COLUMNS} FROM api_keys ORDER BY created_at DESC"))
        return [dict(r._mapping) for r in rows]


def create_key(engine, *, name: str, owner: str, label: str | None,
               scopes: list[str], rpm: int, expires_in_days: int = 0) -> tuple[dict, str]:
    """Insert a key; returns (public dict, raw key). Raw key is shown once."""
    invalid = sorted(set(scopes) - VALID_SCOPES)
    if invalid:
        raise DBError(f"Invalid scopes: {invalid}. Valid: {sorted(VALID_SCOPES)}")
    if label and _find_by_label(engine, label):
        raise DBError("Key already exists for this label")

    raw = generate_api_key()
    expires_at = utcnow() + timedelta(days=expires_in_days) if expires_in_days > 0 else None
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO api_keys (name, owner, label, prefix, key_hash, scopes, rpm, expires_at) "
                "VALUES (:name, :owner, :label, :prefix, :key_hash, :scopes, :rpm, :expires_at) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "name": name,
                "owner": owner,
                "label": label,
                "prefix": raw[:12],
                "key_hash": hash_key(raw),
                "scopes": scopes,
                "rpm": rpm,
                "expires_at": expires_at,
            },
        )
        return dict(result.one()._mapping), raw


def revoke_key(engine, key_id_or_prefix: str) -> dict:
    return _set_revoked(engine, key_id_or_prefix, revoked=True)


def enable_key(engine, key_id_or_prefix: str) -> dict:
    return _set_revoked(engine, key_id_or_prefix, revoked=False)


def _find_by_label(engine, label: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM api_keys WHERE label = :label"), {"label": label}
        ).first()
        return row is not None


def _set_revoked(engine, key_id_or_prefix: str, *, revoked: bool) -> dict:
    try:
        where = "id = :kid"
        kid = int(key_id_or_prefix)
    except (ValueError, TypeError):
        where = "prefix = :kid"
        kid = key_id_or_prefix
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"UPDATE api_keys SET enabled = :enabled, revoked_at = :revoked_at "
                f"WHERE {where} RETURNING prefix"
            ),
            {
                "enabled": not revoked,
                "revoked_at": utcnow() if revoked else None,
                "kid": kid,
            },
        ).first()
        if row is None:
            raise DBError("API key not found")
        return {"prefix": row[0], "status": "revoked" if revoked else "enabled"}

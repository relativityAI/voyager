"""Admin API-key management endpoints (guarded by VOYAGER_ADMIN_KEY)."""

from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.auth.models import (
    DEFAULT_SCOPES,
    VALID_SCOPES,
    APIKey,
    generate_api_key,
    hash_key,
    create_api_key,
    find_by_label,
    find_api_key_by_id_or_prefix,
    list_api_keys,
    update_api_key,
)
from src.auth.rate_limit import check_admin_key_rate_limit
from src.auth.security import require_admin_key
from src.db.models import APIKey as APIKeyModel
from src.utils.helpers import utcnow

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateKeyBody(BaseModel):
    label: Optional[str] = Field(default=None, description="Human-readable label (e.g. user ID)")
    name: str = Field(default="", min_length=0)
    owner: str = ""
    scopes: List[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    rpm: int = Field(default=60, ge=1, le=10000)
    expires_in_days: Optional[int] = Field(default=None, ge=1)


class CreateKeyResponse(BaseModel):
    key: str
    label: Optional[str] = None
    created_at: str


@router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_api_key_endpoint(
    body: CreateKeyBody,
    _: str = Depends(require_admin_key),
) -> CreateKeyResponse:
    invalid = set(body.scopes) - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid scopes: {sorted(invalid)}. Valid: {sorted(VALID_SCOPES)}",
        )

    if body.label:
        existing = await find_by_label(body.label)
        if existing:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Key already exists for this label",
            )

    await check_admin_key_rate_limit()

    raw = generate_api_key()
    expires_at = (
        utcnow() + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None
    )
    api_key = APIKeyModel(
        name=body.name,
        owner=body.owner,
        label=body.label,
        prefix=raw[:12],
        key_hash=hash_key(raw),
        scopes=body.scopes,
        rpm=body.rpm,
        expires_at=expires_at,
    )
    await create_api_key(api_key)
    return CreateKeyResponse(
        key=raw,
        label=api_key.label,
        created_at=api_key.created_at.isoformat(),
    )


@router.get("/keys")
async def list_api_keys_endpoint(
    _: str = Depends(require_admin_key),
    label: Optional[str] = Query(default=None, description="Filter by label"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> List[dict]:
    keys = await list_api_keys(label=label, offset=offset, limit=limit)
    return [k.to_public_dict() for k in keys]


@router.get("/keys/{key_id}")
async def get_api_key(
    key_id: str,
    _: str = Depends(require_admin_key),
) -> dict:
    key = await find_api_key_by_id_or_prefix(key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    return key.to_public_dict()


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    _: str = Depends(require_admin_key),
) -> dict:
    key = await find_api_key_by_id_or_prefix(key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    if key.is_revoked:
        return {"prefix": key.prefix, "status": "already_revoked"}
    await update_api_key(key.id, revoked_at=utcnow(), enabled=False)
    return {"prefix": key.prefix, "status": "revoked"}


@router.post("/keys/{key_id}/enable")
async def enable_api_key(
    key_id: str,
    _: str = Depends(require_admin_key),
) -> dict:
    key = await find_api_key_by_id_or_prefix(key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    await update_api_key(key.id, enabled=True, revoked_at=None)
    return {"prefix": key.prefix, "status": "enabled"}

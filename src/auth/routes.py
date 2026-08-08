"""Admin API-key management endpoints (guarded by VOYAGER_ADMIN_KEY)."""

from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.auth.models import (
    DEFAULT_SCOPES,
    VALID_SCOPES,
    APIKey,
    generate_api_key,
    hash_key,
)
from src.auth.security import require_admin_key
from src.utils.helpers import utcnow

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1)
    owner: str = ""
    scopes: List[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    rpm: int = Field(default=60, ge=1, le=10000)
    expires_in_days: Optional[int] = Field(default=None, ge=1)


class CreateKeyResponse(BaseModel):
    key: str
    api_key: dict


@router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_api_key(
    body: CreateKeyBody,
    _: str = Depends(require_admin_key),
) -> CreateKeyResponse:
    invalid = set(body.scopes) - VALID_SCOPES
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid scopes: {sorted(invalid)}. Valid: {sorted(VALID_SCOPES)}",
        )

    raw = generate_api_key()
    expires_at = (
        utcnow() + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None
    )
    api_key = APIKey(
        name=body.name,
        owner=body.owner,
        prefix=raw[:12],
        key_hash=hash_key(raw),
        scopes=body.scopes,
        rpm=body.rpm,
        expires_at=expires_at,
    )
    await api_key.insert()
    return CreateKeyResponse(key=raw, api_key=api_key.to_public_dict())


@router.get("/keys")
async def list_api_keys(_: str = Depends(require_admin_key)) -> List[dict]:
    keys = await APIKey.find_all().sort("-created_at").to_list()
    return [k.to_public_dict() for k in keys]


@router.delete("/keys/{prefix}")
async def revoke_api_key(
    prefix: str,
    _: str = Depends(require_admin_key),
) -> dict:
    api_key = await APIKey.find_one(APIKey.prefix == prefix)
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    if api_key.is_revoked:
        return {"prefix": prefix, "status": "already_revoked"}
    await api_key.set({"revoked_at": utcnow(), "enabled": False})
    return {"prefix": prefix, "status": "revoked"}


@router.post("/keys/{prefix}/enable")
async def enable_api_key(
    prefix: str,
    _: str = Depends(require_admin_key),
) -> dict:
    api_key = await APIKey.find_one(APIKey.prefix == prefix)
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    await api_key.set({"enabled": True, "revoked_at": None})
    return {"prefix": prefix, "status": "enabled"}

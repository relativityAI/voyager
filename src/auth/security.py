import asyncio
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import (
    APIKey as APIKeyModel,
    find_by_key,
    find_api_key_by_id_or_prefix,
    list_api_keys,
    create_api_key,
    update_api_key,
)
from src.auth.rate_limit import check_rate_limit


def require_admin_key(
    x_voyager_admin_key: Optional[str] = Header(default=None),
) -> str:
    expected = os.getenv("VOYAGER_ADMIN_KEY")
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VOYAGER_ADMIN_KEY is not configured on the server",
        )
    if not x_voyager_admin_key or not secrets.compare_digest(
        x_voyager_admin_key, expected
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid admin key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return x_voyager_admin_key


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return None


async def get_current_api_key(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
) -> APIKeyModel:
    raw = x_api_key or _extract_bearer(authorization)
    if not raw:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key. Provide it via 'X-API-Key' or 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = await find_by_key(raw)
    if api_key is None or not api_key.enabled or api_key.is_revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    if api_key.is_expired:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key has expired")

    await check_rate_limit(api_key)

    asyncio.create_task(_mark_used(api_key))
    return api_key


async def _mark_used(api_key: APIKeyModel) -> None:
    try:
        from src.utils.helpers import utcnow
        await update_api_key(api_key.id, last_used_at=utcnow())
    except Exception:
        logger.debug("Failed to update last_used_at for key", exc_info=True)


async def require_api_key(key: APIKeyModel = Depends(get_current_api_key)) -> APIKeyModel:
    return key


def require_scope(scope: str):
    async def dependency(key: APIKeyModel = Depends(get_current_api_key)) -> APIKeyModel:
        scopes = key.scopes or []
        if scope not in scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This key requires the '{scope}' scope",
            )
        return key

    return dependency

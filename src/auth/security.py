import asyncio
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from loguru import logger

from src.auth.models import APIKey
from src.auth.rate_limit import check_rate_limit


def require_admin_key(
    x_voyager_admin_key: Optional[str] = Header(default=None),
) -> str:
    """Guard admin endpoints with the VOYAGER_ADMIN_KEY env secret."""
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
) -> APIKey:
    """Resolve the API key from X-API-Key or Authorization: Bearer."""
    raw = x_api_key or _extract_bearer(authorization)
    if not raw:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key. Provide it via 'X-API-Key' or 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = await APIKey.find_by_key(raw)
    if api_key is None or not api_key.enabled or api_key.is_revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    if api_key.is_expired:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key has expired")

    await check_rate_limit(api_key)

    # Fire-and-forget last-used update so it never blocks the response.
    asyncio.create_task(_mark_used(api_key))
    return api_key


async def _mark_used(api_key: APIKey) -> None:
    try:
        from src.utils.helpers import utcnow

        await api_key.set({"last_used_at": utcnow()})
    except Exception:
        logger.debug("Failed to update last_used_at for key", exc_info=True)


async def require_api_key(key: APIKey = Depends(get_current_api_key)) -> APIKey:
    return key


def require_scope(scope: str):
    """Build a dependency requiring a specific scope on the API key."""

    async def dependency(key: APIKey = Depends(get_current_api_key)) -> APIKey:
        if scope not in key.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This key requires the '{scope}' scope",
            )
        return key

    return dependency

import secrets
from datetime import datetime
from hashlib import sha256
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.db.models import APIKey as APIKeyModel
from src.db.engine import get_session_factory
from src.utils.helpers import utcnow

APIKey = APIKeyModel

KEY_PREFIX = "vgr_"
DEFAULT_SCOPES = ["data:read"]
VALID_SCOPES = {"data:read", "data:write", "admin"}


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return sha256(key.encode("utf-8")).hexdigest()


async def find_by_key(raw_key: str) -> Optional[APIKeyModel]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_hash == hash_key(raw_key))
        )
        return result.scalar_one_or_none()


async def find_by_label(label: str) -> Optional[APIKeyModel]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(APIKeyModel).where(APIKeyModel.label == label)
        )
        return result.scalar_one_or_none()


async def find_api_key_by_id_or_prefix(key_id: str) -> Optional[APIKeyModel]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            int_id = int(key_id)
        except (ValueError, TypeError):
            int_id = None

        if int_id is not None:
            result = await session.execute(
                select(APIKeyModel).where(
                    or_(APIKeyModel.id == int_id, APIKeyModel.prefix == key_id)
                )
            )
        else:
            result = await session.execute(
                select(APIKeyModel).where(APIKeyModel.prefix == key_id)
            )
        return result.scalar_one_or_none()


async def list_api_keys(
    label: Optional[str] = None, offset: int = 0, limit: int = 50
) -> List[APIKeyModel]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(APIKeyModel)
        if label is not None:
            stmt = stmt.where(APIKeyModel.label == label)
        stmt = stmt.order_by(APIKeyModel.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def create_api_key(key: APIKeyModel) -> APIKeyModel:
    factory = get_session_factory()
    async with factory() as session:
        session.add(key)
        await session.commit()
        await session.refresh(key)
        return key


async def update_api_key(key_id: int, **kwargs) -> Optional[APIKeyModel]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(APIKeyModel).where(APIKeyModel.id == key_id)
        )
        key = result.scalar_one_or_none()
        if key is None:
            return None
        for k, v in kwargs.items():
            setattr(key, k, v)
        await session.commit()
        await session.refresh(key)
        return key

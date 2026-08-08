import secrets
from datetime import datetime
from hashlib import sha256
from typing import List, Optional

from beanie import Document, Indexed
from pydantic import Field

from src.utils.helpers import utcnow

KEY_PREFIX = "vgr_"
DEFAULT_SCOPES = ["data:read"]
VALID_SCOPES = {"data:read", "data:write", "admin"}


def generate_api_key() -> str:
    """Return a new opaque API key. The raw value is shown to the caller once."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """Hash a raw API key. Only the hash is stored."""
    return sha256(key.encode("utf-8")).hexdigest()


class APIKey(Document):
    """A service-account API key (stored hashed)."""

    name: str
    owner: str = ""
    prefix: str
    key_hash: str = Indexed(unique=True)
    scopes: List[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    rpm: int = Field(default=60, ge=1)
    enabled: bool = True
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "api_keys"
        indexes = ["prefix"]

    @classmethod
    async def find_by_key(cls, raw_key: str) -> Optional["APIKey"]:
        return await cls.find_one(cls.key_hash == hash_key(raw_key))

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at < utcnow()

    def to_public_dict(self) -> dict:
        """Safe representation that never leaks the raw key or its hash."""
        return {
            "id": str(self.id),
            "name": self.name,
            "owner": self.owner,
            "prefix": self.prefix,
            "scopes": self.scopes,
            "rpm": self.rpm,
            "enabled": self.enabled,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat()
            if self.last_used_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

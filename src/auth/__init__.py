"""Service-to-service API key authentication for Voyager.

Voyager is called by the user's own application (which owns end-user auth),
so Voyager only issues service-account API keys. End users never talk to
Voyager directly and never receive a Voyager key.
"""

from .models import DEFAULT_SCOPES, KEY_PREFIX, APIKey, generate_api_key, hash_key
from .security import (
    get_current_api_key,
    require_admin_key,
    require_api_key,
    require_scope,
)

__all__ = [
    "APIKey",
    "DEFAULT_SCOPES",
    "KEY_PREFIX",
    "generate_api_key",
    "hash_key",
    "get_current_api_key",
    "require_api_key",
    "require_admin_key",
    "require_scope",
]

"""Client configuration from env vars / CLI flags."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    base_url: str = ""
    api_key: str = ""
    admin_key: str = ""


def load_config(
    base_url: str | None = None,
    api_key: str | None = None,
    admin_key: str | None = None,
) -> Config:
    return Config(
        base_url=(base_url or os.getenv("VOYAGER_BASE_URL", "")).rstrip("/"),
        api_key=api_key or os.getenv("VOYAGER_API_KEY", ""),
        admin_key=admin_key or os.getenv("VOYAGER_ADMIN_KEY", ""),
    )

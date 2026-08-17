"""Configuration persistence for the Voyager admin panel.

Precedence (highest wins):
    1. Sidebar runtime input (handled in app.py via session_state)
    2. Saved config file (~/.config/voyager/panel.json)
    3. Environment / repo .env (VOYAGER_BASE_URL, VOYAGER_API_KEY,
       VOYAGER_ADMIN_KEY, DATABASE_URL)
    4. Hardcoded fallbacks

The config file only stores what the user explicitly saves; env values are
never written back to it so secrets don't linger on disk unless requested.
"""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(os.getenv("VOYAGER_PANEL_CONFIG_DIR", "~/.config/voyager")).expanduser()
CONFIG_FILE = CONFIG_DIR / "panel.json"

DEFAULTS = {
    "api_base_url": "http://localhost:8001",
    "api_key": "",
    "admin_key": "",
    "database_url": "",
}


@dataclass
class PanelConfig:
    api_base_url: str = DEFAULTS["api_base_url"]
    api_key: str = DEFAULTS["api_key"]
    admin_key: str = DEFAULTS["admin_key"]
    database_url: str = DEFAULTS["database_url"]


def env_defaults() -> PanelConfig:
    cfg = PanelConfig()
    for f in fields(PanelConfig):
        val = _env_value(f.name)
        if val:
            setattr(cfg, f.name, val)
    cfg.api_base_url = cfg.api_base_url.rstrip("/")
    return cfg


def load() -> PanelConfig:
    cfg = _load_file() or PanelConfig()
    for f in fields(PanelConfig):
        val = _env_value(f.name)
        if val:
            setattr(cfg, f.name, val)
    cfg.api_base_url = cfg.api_base_url.rstrip("/")
    return cfg


def save(cfg: PanelConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    data["api_base_url"] = data["api_base_url"].rstrip("/")
    CONFIG_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CONFIG_FILE, 0o600)


def clear() -> None:
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()


def _load_file() -> PanelConfig | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text())
        known = {f.name for f in fields(PanelConfig)}
        return PanelConfig(**{k: v for k, v in data.items() if k in known})
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


_ENV_VARS = {
    "api_base_url": "VOYAGER_BASE_URL",
    "api_key": "VOYAGER_API_KEY",
    "admin_key": "VOYAGER_ADMIN_KEY",
    "database_url": "DATABASE_URL",
}


def _env_value(field_name: str) -> str | None:
    val = os.getenv(_ENV_VARS[field_name])
    return val if val else None

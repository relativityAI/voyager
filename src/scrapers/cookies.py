"""Cookie store abstraction for stealth sessions.

The primed session cookie (e.g. NSE's ``nseappid``) is bound to our IP +
fingerprint, so it is worth persisting across process restarts. Because pulls
may run on remote/ephemeral servers (Render) where the filesystem is read-only
or ephemeral, the store is pluggable with a fallback ladder (see D-04):

1. ``file``   - JSON jar on disk (local pulls; env-overridable path).
2. ``memory`` - no persistence; re-prime once per process (safe on Render).
3. ``mongo``  - (extension point, not built yet) store in the Voyager DB.

Any write failure degrades gracefully to in-memory with a warning - scraping
never crashes because the filesystem is read-only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_DIR = "data/cookies"


class CookieStore:
    """Base class for persisting a flat ``{name: value}`` cookie map."""

    def load(self) -> Dict[str, str]:
        raise NotImplementedError

    def save(self, cookies: Dict[str, str]) -> None:
        raise NotImplementedError


class MemoryCookieStore(CookieStore):
    """In-process cookies; nothing survives a restart."""

    def __init__(self) -> None:
        self._cookies: Dict[str, str] = {}

    def load(self) -> Dict[str, str]:
        return dict(self._cookies)

    def save(self, cookies: Dict[str, str]) -> None:
        self._cookies = dict(cookies)


class FileCookieStore(CookieStore):
    """JSON cookie jar on disk with graceful degradation to memory."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._memory: Dict[str, str] = {}
        self._writable: Optional[bool] = None

    def load(self) -> Dict[str, str]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                self._memory = data if isinstance(data, dict) else {}
        except (OSError, ValueError) as exc:
            logger.warning(f"Cookie store unreadable at {self.path}: {exc}")
            self._memory = {}
        return dict(self._memory)

    def save(self, cookies: Dict[str, str]) -> None:
        self._memory = dict(cookies)
        if self._writable is False:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(cookies))
            self._writable = True
        except OSError as exc:
            self._writable = False
            logger.warning(
                f"Cookie store not writable at {self.path}; "
                f"keeping cookies in memory only: {exc}"
            )


def create_cookie_store(
    source: str,
    cookie_dir: Optional[Path] = None,
    cookie_store: Optional[str] = None,
) -> CookieStore:
    """Build the configured cookie store for a source.

    Resolution order: ``NSE_COOKIE_STORE`` env (``file`` | ``memory``) → default
    ``file`` (local pulls) at ``data/cookies/<source>.json``.
    """
    source_upper = source.upper()
    mode = (cookie_store or os.getenv(f"{source_upper}_COOKIE_STORE", "file")).lower()

    if mode == "memory":
        return MemoryCookieStore()

    path_env = os.getenv(f"{source_upper}_COOKIE_PATH")
    directory = cookie_dir or Path(
        path_env or os.path.join(DEFAULT_COOKIE_DIR, f"{source}_cookies.json")
    )
    return FileCookieStore(directory)


__all__ = ["CookieStore", "MemoryCookieStore", "FileCookieStore", "create_cookie_store"]

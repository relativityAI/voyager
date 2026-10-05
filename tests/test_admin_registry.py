"""The admin Playground registry is a hand-maintained mirror of the API.

These two checks are the whole reason that mirror is tolerable: every route
the app serves is listed, and every listed entry is a route the app serves.
Without them the panel rots silently the first time an endpoint is added.
"""

import re

from admin_panel.registry import ALL_ENDPOINTS
from api import app

# Path-param names differ between the registry and the routes (it says
# {prefix} where the route says {key_id}) and the panel substitutes into its
# own template, so compare shapes, not names.
_SHAPE = re.compile(r"\{[^}]+\}")

# FastAPI's own docs pages are not panel endpoints, /metrics is registered
# only when METRICS_ENABLED is truthy, and Starlette auto-adds HEAD to GETs.
_SKIP = {"/metrics", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
_SKIP_METHODS = {"HEAD"}


def _live() -> set[tuple[str, str]]:
    out = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods or route.path in _SKIP:
            continue
        for method in methods - _SKIP_METHODS:
            out.add((method, _SHAPE.sub("{}", route.path)))
    return out


def _listed() -> set[tuple[str, str]]:
    return {
        (e["method"], _SHAPE.sub("{}", e["path"]))
        for e in ALL_ENDPOINTS
        if e["path"] not in _SKIP
    }


def test_every_live_route_is_listed():
    assert _live() - _listed() == set()


def test_every_listed_entry_is_a_live_route():
    assert _listed() - _live() == set()
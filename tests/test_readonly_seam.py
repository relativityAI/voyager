"""Seam guards for the read-only split.

Two invariants keep the architecture honest:
  1. The serving API exposes no write routes (every APIRoute is GET/HEAD).
  2. The read path (api.py, src/services, mcp_server) never imports
     src.ingest — writes flow only through the CLI-driven ingest package.
"""

import ast
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from api import app

_ROOT = Path(__file__).resolve().parent.parent


def test_api_exposes_only_get_routes():
    writers = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if isinstance(route, APIRoute)
        and not route.methods <= {"GET", "HEAD"}
    ]
    assert writers == [], f"non-GET routes still mounted: {writers}"


def _assert_no_ingest_imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for mod in names:
            assert "ingest" not in mod.split("."), (
                f"{path.relative_to(_ROOT)} imports the write path ({mod})"
            )


@pytest.mark.parametrize(
    "module",
    sorted(str(p.relative_to(_ROOT)) for p in (_ROOT / "src" / "services").glob("*.py")),
)
def test_services_never_import_ingest(module):
    _assert_no_ingest_imports(_ROOT / module)


def test_api_never_imports_ingest():
    _assert_no_ingest_imports(_ROOT / "api.py")


def test_mcp_server_never_imports_ingest():
    _assert_no_ingest_imports(_ROOT / "mcp_server.py")


def test_ingest_package_exists_and_exports_write_path():
    import src.ingest as ingest

    for name in (
        "pull_nse_data",
        "pull_sec_data",
        "download_and_store",
        "refresh_news",
        "refresh_shareholdings",
        "ingest_document",
    ):
        assert callable(getattr(ingest, name)), f"src.ingest.{name} missing"

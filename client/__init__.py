"""Voyager remote client package.

Talks to a deployed Voyager API over HTTPS using an API key. Run from the
repo root with:

    python -m client --help
"""

from .cli import app

__all__ = ["app"]

import json
import os
from typing import Any, Dict, Set

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
METRICS_CONFIG_PATH = os.path.join(ASSETS_DIR, "metrics_config.json")

_PRIORITY_CACHE: Dict[str, Set[str]] | None = None


class ServiceError(Exception):
    """Base class for service-layer errors surfaced to the API/CLI."""

    status_code = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnsupportedSourceError(ServiceError):
    status_code = 501


class NotFoundError(ServiceError):
    status_code = 404


class InvalidRequestError(ServiceError):
    status_code = 400


class ServiceUnavailableError(ServiceError):
    status_code = 503


class UpstreamError(ServiceError):
    status_code = 502


def _load_priority_metrics() -> Dict[str, Set[str]]:
    global _PRIORITY_CACHE
    if _PRIORITY_CACHE is not None:
        return _PRIORITY_CACHE
    try:
        with open(METRICS_CONFIG_PATH) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    result: Dict[str, Set[str]] = {}
    for stmt_key, cfg in config.items():
        result[stmt_key] = set(cfg.get("priority", []))
    _PRIORITY_CACHE = result
    return result


_PRIORITY_FIELD_KEEP = {
    "symbol",
    "period_end_date",
    "period_start_date",
    "xbrl_url",
    "broadcast_date",
    "consolidated",
    "measure",
    "entity_identifier",
    "fiscal_period",
    "filing_type",
    "source_endpoint",
    "context_ref_type",
    "pulled_at",
}


def _filter_priority_fields(
    doc: Dict[str, Any], priority_set: Set[str], all_fields: bool
) -> Dict[str, Any]:
    if all_fields:
        return doc
    filtered = {}
    for k, v in doc.items():
        if k in priority_set or k in _PRIORITY_FIELD_KEEP:
            filtered[k] = v
    return filtered

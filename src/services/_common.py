import json
import os
from typing import Any, Dict, Optional, Set, Tuple

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


# A data source implies the country it serves; callers no longer pass both.
SOURCE_COUNTRY: Dict[str, str] = {
    "NSE": "in",
    "SEC": "us",
}


def _validate_source(country: Optional[str], source: str) -> Tuple[str, str]:
    """Normalize source and reject unsupported combos. Returns (country, source).

    ``country`` is optional and derived from ``source`` when omitted.
    """
    source = source.upper()
    if source not in SOURCE_COUNTRY:
        raise UnsupportedSourceError(f"Data source '{source}' is not yet supported")
    if country is None:
        country = SOURCE_COUNTRY[source]
    else:
        country = country.lower()
        if SOURCE_COUNTRY[source] != country:
            raise UnsupportedSourceError(
                f"Source '{source}' does not serve country '{country}'"
            )
    return country, source

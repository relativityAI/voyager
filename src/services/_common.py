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


class UnsupportedCountryError(ServiceError):
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


# Fields always kept in filtered (default) responses. Scraper internals with
# raw XBRL noise (source_endpoint, context_ref_type dimension lists) are
# excluded by default — pass all_fields=true to see them (audit P1-6).
# entity_identifier is excluded too: it duplicated symbol for NSE and was
# hardcoded None for SEC. broadcast_date, measure and pulled_at are ingestion
# provenance, not statement data.
_PRIORITY_FIELD_KEEP = {
    "symbol",
    "period_end_date",
    "period_start_date",
    "xbrl_url",
    "consolidated",
    "fiscal_period",
    "filing_type",
    "data_quality",
}


def _derive_fields(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in fields that are exact functions of values already on the row.

    Runs for both filtered and ``all_fields`` responses so the derived set does
    not depend on the projection. Each derivation is skipped when any input is
    missing or unusable, leaving the key absent rather than emitting a guess.
    """
    # Weighted-average shares: where the source tags them (SEC) the stored
    # column wins and this is a no-op. NSE filings carry no such tag, so divide
    # profit by EPS. EPS is struck on income attributable to the parent, so
    # prefer that numerator over the post-minority total.
    #
    # ponytail: EPS is stored at 2 decimal places, so derived share counts carry
    # up to ~0.5% error (AAPL diluted: 14,675M derived vs 14,746M reported).
    # Ceiling = EPS rounding. Upgrade: map
    # us-gaap_WeightedAverageNumberOfSharesOutstandingBasic/Diluted for every
    # source, which already exists in the SEC pulls.
    numerator = doc.get("profit_or_loss_attributable_to_owners_of_parent")
    if numerator is None:
        numerator = doc.get("profit_loss_for_period")
    if numerator is not None:
        for shares_col, eps_col in (
            ("weighted_average_shares_basic", "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"),
            ("weighted_average_shares_diluted", "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"),
        ):
            if doc.get(shares_col) is not None:
                continue
            eps = doc.get(eps_col)
            if eps:
                doc[shares_col] = numerator / eps

    # Total equity including non-controlling interests is the reported anchor;
    # parent-only equity is a separate reported tag. XBRL US guidance confirms
    # the difference is minority interest when both are present.
    total = doc.get("total_equity_including_nci")
    if total is not None:
        doc.setdefault("total_equity", total)
        parent = doc.get("stockholders_equity")
        if parent is not None:
            doc["minority_interest"] = total - parent

    return doc


def _filter_priority_fields(
    doc: Dict[str, Any], priority_set: Set[str], all_fields: bool
) -> Dict[str, Any]:
    doc = _derive_fields(doc)
    if all_fields:
        return doc
    return {
        k: v
        for k, v in doc.items()
        if (k in priority_set or k in _PRIORITY_FIELD_KEEP) and v is not None
    }


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

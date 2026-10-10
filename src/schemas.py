"""Response shapes advertised in the OpenAPI spec (the agent-facing contract).

Attached by the OpenAPI hook in ``api.py``. These document top-level
structure only: ``extra="allow"`` mirrors the dynamic metric/key payloads
real responses carry, and the models are never used to validate live bodies
(attachment is schema-only, so byte-level output cannot change).
"""

from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict


class Doc(BaseModel):
    model_config = ConfigDict(extra="allow")


class OkResponse(Doc):
    ok: Union[int, bool]


class ListResponse(Doc):
    category: str
    country: str
    source: str
    data: List[Union[str, dict]]


class SearchResponse(Doc):
    query: str
    source: str
    count: int
    results: List[dict]


class Snapshot(Doc):
    """Symbol-scoped payload: identified by symbol/source, keys vary by endpoint."""

    symbol: Optional[str] = None
    source: Optional[str] = None


class Pagination(Doc):
    total: int
    offset: int
    limit: int
    returned: int


class StatementPage(Doc):
    pagination: Pagination


class JobAccepted(Doc):
    job_id: str
    status: str
    status_url: str


class JobPublic(Doc):
    job_id: str
    symbol: Optional[str] = None
    task: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


class Bar(Doc):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class HistoryResponse(Doc):
    symbol: str
    period: str
    interval: str
    bars: int
    data: List[Bar]


class MetricsBatch(Doc):
    source: str
    consolidated: bool
    filing_type: str
    count: int
    metrics: dict


class Announcements(Doc):
    symbol: str
    source: str
    market: str
    announcements: List[dict]


class KeyPublic(Doc):
    id: Optional[str] = None
    name: str
    owner: Optional[str] = None
    label: Optional[str] = None
    prefix: str
    scopes: List[str]
    rpm: int
    enabled: bool
    created_at: Optional[str] = None


class MacroIndex(Doc):
    symbol: Optional[str] = None
    name: Optional[str] = None
    last: Optional[float] = None
    change_pct: Optional[float] = None


class MacroOverview(Doc):
    country: str
    as_of: str
    indices: List[MacroIndex]
    flows: Optional[dict] = None
    turnover_cash_crore: Optional[float] = None
    policy_repo_rate_pct: Optional[float] = None


class MacroIndices(Doc):
    country: str
    as_of: str
    count: int
    indices: List[MacroIndex]


class MacroBar(Doc):
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None


class MacroHistory(Doc):
    country: str
    symbol: str
    source: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    bars: List[MacroBar]
    bars_available: Optional[int] = None
    downsampled: Optional[bool] = None


class MacroConstituents(Doc):
    country: str
    symbol: str
    count: int
    constituents: List[dict]


class MacroIndexQuoteOrSnapshot(Doc):
    country: str
    index: Optional[MacroIndex] = None
    as_of: Optional[str] = None


class MacroValuationOrBreadth(Doc):
    country: str
    as_of: Optional[str] = None
    date: Optional[str] = None
    total: Optional[int] = None
    data: Optional[List[dict]] = None
    bars_available: Optional[int] = None
    downsampled: Optional[bool] = None


class MacroFlows(Doc):
    country: str
    as_of: str
    fii_dii: List[dict]


class MacroTurnover(Doc):
    country: str
    as_of: str
    turnover: List[dict]


class MacroDerivatives(Doc):
    country: str
    underlying: str
    as_of: str
    spot: Optional[float] = None
    expiry_date: Optional[str] = None
    total_oi_lots: Optional[float] = None
    change_oi_pct: Optional[float] = None
    put_call_ratio: Optional[float] = None
    maxpain: Optional[float] = None


class MacroRates(Doc):
    country: str
    as_of: str
    provider: str
    rates: dict

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

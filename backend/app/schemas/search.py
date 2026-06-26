"""Pydantic request/response schemas for the global search endpoint (M5 Step 6).

Schemas are thin wire DTOs; business logic lives in the service layer.

``SearchResponse`` (§4.8) — the top-level response for ``GET /search``:
    ``item_definitions``  List of ``DefinitionSearchHit`` (id + name).
    ``stock_instances``   List of ``InstanceSearchHit`` (id + definition info +
                          durable-identity fields).
    ``locations``         List of ``LocationSearchHit`` (id + name).
    ``categories``        List of ``CategorySearchHit`` (id + name).
    ``tags``              List of ``TagSearchHit`` (id + name + color).
    ``totals``            ``SearchTotals`` — true match count per type.
"""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Per-type summary schemas (lightweight — enough to render + link)
# ---------------------------------------------------------------------------


class DefinitionSearchHit(BaseModel):
    """Lightweight item-definition hit for the search response."""

    id: int
    name: str


class InstanceSearchHit(BaseModel):
    """Lightweight stock-instance hit for the search response."""

    id: int
    definition_id: int
    definition_name: str
    serial: str | None = None
    model_number: str | None = None
    manufacturer: str | None = None


class LocationSearchHit(BaseModel):
    """Lightweight location hit for the search response."""

    id: int
    name: str


class CategorySearchHit(BaseModel):
    """Lightweight category hit for the search response."""

    id: int
    name: str


class TagSearchHit(BaseModel):
    """Lightweight tag hit for the search response."""

    id: int
    name: str
    color: str | None = None


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


class SearchTotals(BaseModel):
    """True match count per type (may exceed the capped result list length).

    All fields default to 0 so that a response that only searched a subset of
    types (via the ``types`` query parameter) still has a well-formed totals object.
    """

    item_definitions: int = 0
    stock_instances: int = 0
    locations: int = 0
    categories: int = 0
    tags: int = 0


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------


class SearchResponse(BaseModel):
    """Grouped response for ``GET /search?q=&types=&limit=``.

    Results are grouped by entity type.  Each list is independently capped at
    ``limit`` (default 20).  ``totals`` holds the true match count for each
    type — which may be larger than the capped list.

    Types not included in the ``types`` query-parameter filter have empty lists
    and zero totals.  An empty or whitespace-only ``q`` returns all-empty lists
    with zero totals (no error).
    """

    item_definitions: list[DefinitionSearchHit] = []
    stock_instances: list[InstanceSearchHit] = []
    locations: list[LocationSearchHit] = []
    categories: list[CategorySearchHit] = []
    tags: list[TagSearchHit] = []
    totals: SearchTotals = SearchTotals()

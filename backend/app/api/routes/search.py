"""Global search endpoint (M5 Step 6).

``GET /search?q=&types=&limit=``
    Session-authenticated.  Returns results grouped by entity type, each list
    independently capped at ``limit`` (default 20).

    Parameters
    ----------
    q (str, required)
        Search string.  An empty or whitespace-only ``q`` returns an empty
        ``SearchResponse`` immediately — no DB queries, no error.
    types (str, optional)
        Comma-separated type filter.  Allowed values (case-sensitive):
        ``item_definition``, ``stock_instance``, ``location``, ``category``,
        ``tag``.  Defaults to all types when omitted.  Unknown type identifiers
        are silently ignored (the known subset is searched).
    limit (int, optional, 1–100, default 20)
        Per-type result cap.  Each type's result list has at most ``limit``
        items; ``totals`` reports the true match count.

Error contract
--------------
    401  No/invalid session.
    422  Invalid ``limit`` value (out of range).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.core.errors import ErrorResponse
from app.db.session import get_db
from app.schemas.search import (
    CategorySearchHit,
    DefinitionSearchHit,
    InstanceSearchHit,
    LocationSearchHit,
    SearchResponse,
    SearchTotals,
    TagSearchHit,
)
from app.services.search.like import ALL_TYPES
from app.services.search.service import SearchService, build_search_service

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(tags=["search"], responses=_ERROR_RESPONSES)


def _get_service(db: Annotated[Session, Depends(get_db)]) -> SearchService:
    """Dependency: build and return a SearchService."""
    return build_search_service(db)


@router.get("/search", response_model=SearchResponse)
def global_search(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[SearchService, Depends(_get_service)],
    q: Annotated[str, Query(description="Search query string.")],
    types: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated subset of entity types to search: "
                "item_definition, stock_instance, location, category, tag. "
                "Defaults to all types when omitted."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Per-type result cap (default 20, max 100).",
        ),
    ] = 20,
) -> SearchResponse:
    """Search across all entity types and return grouped, capped results.

    An empty or whitespace-only ``q`` returns an empty response immediately
    (no DB queries).  The ``types`` filter restricts which entity groups are
    populated; unrecognised type identifiers are silently ignored.
    """
    # Short-circuit: blank query → empty response, no DB hit.
    stripped_q = q.strip()
    if not stripped_q:
        return SearchResponse()

    # Resolve requested types: default = all; filter to known set.
    if types is None:
        resolved_types = set(ALL_TYPES)
    else:
        requested = {t.strip() for t in types.split(",") if t.strip()}
        resolved_types = requested & set(ALL_TYPES)

    # If all requested types were unknown, return empty.
    if not resolved_types:
        return SearchResponse()

    results = service.search(stripped_q, resolved_types, limit)

    return SearchResponse(
        item_definitions=[
            DefinitionSearchHit(id=h.id, name=h.name) for h in results.item_definitions
        ],
        stock_instances=[
            InstanceSearchHit(
                id=h.id,
                definition_id=h.definition_id,
                definition_name=h.definition_name,
                serial=h.serial,
                model_number=h.model_number,
                manufacturer=h.manufacturer,
            )
            for h in results.stock_instances
        ],
        locations=[LocationSearchHit(id=h.id, name=h.name) for h in results.locations],
        categories=[CategorySearchHit(id=h.id, name=h.name) for h in results.categories],
        tags=[TagSearchHit(id=h.id, name=h.name, color=h.color) for h in results.tags],
        totals=SearchTotals(
            item_definitions=results.totals.get("item_definitions", 0),
            stock_instances=results.totals.get("stock_instances", 0),
            locations=results.totals.get("locations", 0),
            categories=results.totals.get("categories", 0),
            tags=results.totals.get("tags", 0),
        ),
    )

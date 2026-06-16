"""Location tree CRUD endpoints.

All endpoints require a valid session (via ``get_authenticated_context``).

Routes (all under the api_prefix, e.g. /api):
    GET    /locations               Flat list, optionally filtered by q / parent_id.
    GET    /locations/tree          Full nested tree (recursive LocationTreeNode).
    POST   /locations               Create a location.
    GET    /locations/{id}          Get a single location.
    PATCH  /locations/{id}          Partial update (reparent cycle-checked).
    DELETE /locations/{id}          Delete (guarded — 409 if non-empty).

Error contract:
    404  Location not found / parent not found.
    409  Cycle detected on reparent / delete of non-empty node.
    401  No/invalid session.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.context import RequestContext, get_authenticated_context
from app.db.session import get_db
from app.schemas.location import (
    LocationCreate,
    LocationResponse,
    LocationTreeNode,
    LocationUpdate,
)
from app.services.location import LocationService

router = APIRouter(prefix="/locations", tags=["locations"])


def _get_service(db: Session = Depends(get_db)) -> LocationService:
    """Dependency: build and return a LocationService."""
    return LocationService(db)


@router.get("/tree", response_model=list[LocationTreeNode])
def get_tree(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
) -> list[LocationTreeNode]:
    """Return the full location tree as a nested structure."""
    return service.get_tree()


@router.get("", response_model=list[LocationResponse])
def list_locations(
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
    q: Annotated[str | None, Query(description="Case-insensitive name substring filter.")] = None,
    parent_id: Annotated[
        int | None,
        Query(
            description="Filter by parent_id (pass 0 to get root locations is NOT supported; omit to get all)."
        ),
    ] = None,
) -> list[LocationResponse]:
    """Return a flat list of locations, optionally filtered.

    - ``q``: case-insensitive substring match on the name.
    - ``parent_id``: when provided, return only locations with that parent.
    """
    parent_id_filter = parent_id is not None
    locs = service.list_all(q=q, parent_id=parent_id, parent_id_filter=parent_id_filter)
    return [LocationResponse.model_validate(loc) for loc in locs]


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
def create_location(
    body: LocationCreate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> LocationResponse:
    """Create a new location."""
    loc = service.create(body)
    db.commit()
    db.refresh(loc)
    return LocationResponse.model_validate(loc)


@router.get("/{location_id}", response_model=LocationResponse)
def get_location(
    location_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
) -> LocationResponse:
    """Return a single location by id."""
    loc = service.get(location_id)
    return LocationResponse.model_validate(loc)


@router.patch("/{location_id}", response_model=LocationResponse)
def update_location(
    location_id: int,
    body: LocationUpdate,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> LocationResponse:
    """Partially update a location.

    Reparenting (changing ``parent_id``) is cycle-checked in the service layer.
    """
    loc = service.update(location_id, body)
    db.commit()
    db.refresh(loc)
    return LocationResponse.model_validate(loc)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: int,
    _ctx: Annotated[RequestContext, Depends(get_authenticated_context)],
    service: Annotated[LocationService, Depends(_get_service)],
    db: Session = Depends(get_db),
) -> None:
    """Delete a location.

    Returns 409 if the location still has child locations.
    """
    service.delete(location_id)
    db.commit()
